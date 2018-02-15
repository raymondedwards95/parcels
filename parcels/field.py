from parcels.loggers import logger
from scipy.interpolate import RegularGridInterpolator
from collections import Iterable
from py import path
import numpy as np
import xarray
from ctypes import Structure, c_int, c_float, POINTER, pointer
from netCDF4 import Dataset, num2date
import dask.array as da
from math import cos, pi
from datetime import timedelta, datetime
from dateutil.parser import parse
import math
from grid import (RectilinearZGrid, RectilinearSGrid, CurvilinearZGrid,
                  CurvilinearSGrid, CGrid, GridCode)


__all__ = ['CentralDifferences', 'Field', 'Geographic', 'GeographicPolar', 'GeographicSquare', 'GeographicPolarSquare']


class FieldSamplingError(RuntimeError):
    """Utility error class to propagate erroneous field sampling"""

    def __init__(self, x, y, z, field=None):
        self.field = field
        self.x = x
        self.y = y
        self.z = z
        message = "%s sampled at (%f, %f, %f)" % (
            field.name if field else "Field", self.x, self.y, self.z
        )
        super(FieldSamplingError, self).__init__(message)


class TimeExtrapolationError(RuntimeError):
    """Utility error class to propagate erroneous time extrapolation sampling"""

    def __init__(self, time, field=None):
        if field is not None and field.grid.time_origin != 0:
            time = field.grid.time_origin + timedelta(seconds=time)
        message = "%s sampled outside time domain at time %s." % (
            field.name if field else "Field", time)
        message += " Try setting allow_time_extrapolation to True"
        super(TimeExtrapolationError, self).__init__(message)


def CentralDifferences(field_data, lat, lon):
    """Function to calculate gradients in two dimensions
    using central differences on field

    :param field_data: data to take the gradients of
    :param lat: latitude vector
    :param lon: longitude vector

    :rtype: gradient of data in zonal and meridional direction
    """
    r = 6.371e6  # radius of the earth
    deg2rd = np.pi / 180
    dy = r * np.diff(lat) * deg2rd
    # calculate the width of each cell, dependent on lon spacing and latitude
    dx = np.zeros([len(lon)-1, len(lat)], dtype=np.float32)
    for x in range(len(lon))[1:]:
        for y in range(len(lat)):
            dx[x-1, y] = r * np.cos(lat[y] * deg2rd) * (lon[x]-lon[x-1]) * deg2rd
    # calculate central differences for non-edge cells (with equal weighting)
    dVdx = np.zeros(shape=np.shape(field_data), dtype=np.float32)
    dVdy = np.zeros(shape=np.shape(field_data), dtype=np.float32)
    for x in range(len(lon))[1:-1]:
        for y in range(len(lat)):
            dVdx[x, y] = (field_data[x+1, y] - field_data[x-1, y]) / (2 * dx[x-1, y])
    for x in range(len(lon)):
        for y in range(len(lat))[1:-1]:
            dVdy[x, y] = (field_data[x, y+1] - field_data[x, y-1]) / (2 * dy[y-1])
    # Forward and backward difference for edges
    for x in range(len(lon)):
        dVdy[x, 0] = (field_data[x, 1] - field_data[x, 0]) / dy[0]
        dVdy[x, len(lat)-1] = (field_data[x, len(lat)-1] - field_data[x, len(lat)-2]) / dy[len(lat)-2]
    for y in range(len(lat)):
        dVdx[0, y] = (field_data[1, y] - field_data[0, y]) / dx[0, y]
        dVdx[len(lon)-1, y] = (field_data[len(lon)-1, y] - field_data[len(lon)-2, y]) / dx[len(lon)-2, y]

    return [dVdx, dVdy]


class UnitConverter(object):
    """ Interface class for spatial unit conversion during field sampling
        that performs no conversion.
    """
    source_unit = None
    target_unit = None

    def to_target(self, value, x, y, z):
        return value

    def ccode_to_target(self, x, y, z):
        return "1.0"

    def to_source(self, value, x, y, z):
        return value

    def ccode_to_source(self, x, y, z):
        return "1.0"


class Geographic(UnitConverter):
    """ Unit converter from geometric to geographic coordinates (m to degree) """
    source_unit = 'm'
    target_unit = 'degree'

    def to_target(self, value, x, y, z):
        return value / 1000. / 1.852 / 60.

    def to_source(self, value, x, y, z):
        return value * 1000. * 1.852 * 60.

    def ccode_to_target(self, x, y, z):
        return "(1.0 / (1000.0 * 1.852 * 60.0))"

    def ccode_to_source(self, x, y, z):
        return "(1000.0 * 1.852 * 60.0)"


class GeographicPolar(UnitConverter):
    """ Unit converter from geometric to geographic coordinates (m to degree)
        with a correction to account for narrower grid cells closer to the poles.
    """
    source_unit = 'm'
    target_unit = 'degree'

    def to_target(self, value, x, y, z):
        return value / 1000. / 1.852 / 60. / cos(y * pi / 180)

    def to_source(self, value, x, y, z):
        return value * 1000. * 1.852 * 60. * cos(y * pi / 180)

    def ccode_to_target(self, x, y, z):
        return "(1.0 / (1000. * 1.852 * 60. * cos(%s * M_PI / 180)))" % y

    def ccode_to_source(self, x, y, z):
        return "(1000. * 1.852 * 60. * cos(%s * M_PI / 180))" % y


class GeographicSquare(UnitConverter):
    """ Square distance converter from geometric to geographic coordinates (m2 to degree2) """
    source_unit = 'm2'
    target_unit = 'degree2'

    def to_target(self, value, x, y, z):
        return value / pow(1000. * 1.852 * 60., 2)

    def to_source(self, value, x, y, z):
        return value * pow(1000. * 1.852 * 60., 2)

    def ccode_to_target(self, x, y, z):
        return "pow(1.0 / (1000.0 * 1.852 * 60.0), 2)"

    def ccode_to_source(self, x, y, z):
        return "pow((1000.0 * 1.852 * 60.0), 2)"


class GeographicPolarSquare(UnitConverter):
    """ Square distance converter from geometric to geographic coordinates (m2 to degree2)
        with a correction to account for narrower grid cells closer to the poles.
    """
    source_unit = 'm2'
    target_unit = 'degree2'

    def to_target(self, value, x, y, z):
        return value / pow(1000. * 1.852 * 60. * cos(y * pi / 180), 2)

    def to_source(self, value, x, y, z):
        return value * pow(1000. * 1.852 * 60. * cos(y * pi / 180), 2)

    def ccode_to_target(self, x, y, z):
        return "pow(1.0 / (1000. * 1.852 * 60. * cos(%s * M_PI / 180)), 2)" % y

    def ccode_to_source(self, x, y, z):
        return "pow((1000. * 1.852 * 60. * cos(%s * M_PI / 180)), 2)" % y


class Field(object):
    """Class that encapsulates access to field data.

    :param name: Name of the field
    :param data: 2D, 3D or 4D numpy array of field data
    :param lon: Longitude coordinates (numpy vector or array) of the field (only if grid is None)
    :param lat: Latitude coordinates (numpy vector or array) of the field (only if grid is None)
    :param depth: Depth coordinates (numpy vector or array) of the field (only if grid is None)
    :param time: Time coordinates (numpy vector) of the field (only if grid is None)
    :param mesh: String indicating the type of mesh coordinates and
           units used during velocity interpolation: (only if grid is None)

           1. spherical (default): Lat and lon in degree, with a
              correction for zonal velocity U near the poles.
           2. flat: No conversion, lat/lon are assumed to be in m.
    :param grid: :class:`parcels.grid.Grid` object containing all the lon, lat depth, time
           mesh and time_origin information. Can be constructed from any of the Grid objects
    :param transpose: Transpose data to required (lon, lat) layout
    :param vmin: Minimum allowed value on the field. Data below this value are set to zero
    :param vmax: Maximum allowed value on the field. Data above this value are set to zero
    :param time_origin: Time origin (datetime object) of the time axis (only if grid is None)
    :param interp_method: Method for interpolation. Either 'linear' or 'nearest'
    :param allow_time_extrapolation: boolean whether to allow for extrapolation in time
           (i.e. beyond the last available time snapshot)
    :param time_periodic: boolean whether to loop periodically over the time component of the Field
           This flag overrides the allow_time_interpolation and sets it to False
    """

    unitconverters = {'U': GeographicPolar(), 'V': Geographic(),
                      'Kh_zonal': GeographicPolarSquare(),
                      'Kh_meridional': GeographicSquare()}

    def __init__(self, name, data, lon=None, lat=None, depth=None, time=None, grid=None,
                 transpose=False, vmin=None, vmax=None, time_origin=0,
                 interp_method='linear', allow_time_extrapolation=None, time_periodic=False, mesh='flat'):
        self.name = name
        if self.name == 'UV':
            return
        self.data = data
        if grid:
            self.grid = grid
        else:
            self.grid = RectilinearZGrid(lon, lat, depth, time, time_origin=time_origin, mesh=mesh)
        # self.lon, self.lat, self.depth and self.time are not used anymore in parcels.
        # self.grid should be used instead.
        # Those variables are still defined for backwards compatibility with users codes.
        self.lon = self.grid.lon
        self.lat = self.grid.lat
        self.depth = self.grid.depth
        self.time = self.grid.time
        if self.grid.mesh is 'flat' or (name not in self.unitconverters.keys()):
            self.units = UnitConverter()
        elif self.grid.mesh is 'spherical':
            self.units = self.unitconverters[name]
        else:
            raise ValueError("Unsupported mesh type. Choose either: 'spherical' or 'flat'")
        self.interp_method = interp_method
        self.fieldset = None
        if allow_time_extrapolation is None:
            self.allow_time_extrapolation = True if time is None else False
        else:
            self.allow_time_extrapolation = allow_time_extrapolation

        self.time_periodic = time_periodic
        if self.time_periodic and self.allow_time_extrapolation:
            logger.warning_once("allow_time_extrapolation and time_periodic cannot be used together.\n \
                                 allow_time_extrapolation is set to False")
            self.allow_time_extrapolation = False

        if not isinstance(self.data, da.core.Array):
            # Ensure that field data is the right data type
            if not self.data.dtype == np.float32:
                logger.warning_once("Casting field data to np.float32")
                self.data = self.data.astype(np.float32)
            if transpose:
                # Make a copy of the transposed array to enforce
                # C-contiguous memory layout for JIT mode.
                self.data = np.transpose(self.data).copy()
            if self.grid.zdim > 1:
                self.data = self.data.reshape((self.grid.tdim, self.grid.zdim, self.grid.ydim, self.grid.xdim))
            else:
                self.data = self.data.reshape((self.grid.tdim, self.grid.ydim, self.grid.xdim))

            # Hack around the fact that NaN and ridiculously large values
            # propagate in SciPy's interpolators
            if vmin is not None:
                self.data[self.data < vmin] = 0.
            if vmax is not None:
                self.data[self.data > vmax] = 0.
            self.data[np.isnan(self.data)] = 0.
            self.dataDask = None
        else:
            self.dataDask = self.data
            self.grid.timeFull = self.grid.time
            self.grid.time = self.grid.time[0:3]
            self.grid.tdim = 3
            self.data = self.dataDask[0:3, :].compute()
            self.data = self.data.astype(np.float32)
            self.grid.timeInd = 0

        # Variable names in JIT code
        self.ccode_data = self.name

    @classmethod
    def from_netcdf(cls, name, dimensions, filenames, indices={},
                    allow_time_extrapolation=False, mesh='flat', **kwargs):
        """Create field from netCDF file

        :param name: Name of the field to create
        :param dimensions: Dictionary mapping variable names for the relevant dimensions in the NetCDF file
        :param filenames: list of filenames to read for the field.
               Note that wildcards ('*') are also allowed
        :param indices: dictionary mapping indices for each dimension to read from file.
               This can be used for reading in only a subregion of the NetCDF file
        :param allow_time_extrapolation: boolean whether to allow for extrapolation in time
               (i.e. beyond the last available time snapshot
        :param mesh: String indicating the type of mesh coordinates and
               units used during velocity interpolation:

               1. spherical (default): Lat and lon in degree, with a
                  correction for zonal velocity U near the poles.
               2. flat: No conversion, lat/lon are assumed to be in m.
        """

        if not isinstance(filenames, Iterable) or isinstance(filenames, str):
            filenames = [filenames]
        with FileBuffer(filenames[0], dimensions) as filebuffer:
            lon, lat = filebuffer.read_lonlat(indices)
            depth = filebuffer.read_depth(indices)
            # Assign time_units if the time dimension has units and calendar
            time_units = filebuffer.time_units
            calendar = filebuffer.calendar
            if name in ['cosU', 'sinU', 'cosV', 'sinV']:
                warning = False
                try:
                    source = filebuffer.dataset.source
                    if source != 'parcels_compute_curvilinearGrid_rotationAngles':
                        warning = True
                except:
                    warning = True
                if warning:
                    logger.warning_once("You are defining a field name 'cosU', 'sinU', 'cosV' or 'sinV' which was not generated by Parcels. This field will be used to rotate UV velocity at interpolation")

        # Concatenate time variable to determine overall dimension
        # across multiple files
        timeslices = []
        for fname in filenames:
            with FileBuffer(fname, dimensions) as filebuffer:
                timeslices.append(filebuffer.time)
        timeslices = np.array(timeslices)
        time = np.concatenate(timeslices)
        if time_units is None:
            time_origin = 0
        else:
            time_origin = num2date(0, time_units, calendar)
            if type(time_origin) is not datetime:
                # num2date in some cases returns a 'phony' datetime. In that case,
                # parse it as a string.
                # See http://unidata.github.io/netcdf4-python/#netCDF4.num2date
                time_origin = parse(str(time_origin))

        # Pre-allocate data before reading files into buffer
        # ## depthdim = depth.size if len(depth.shape) == 1 else depth.shape[-3]
        # ## latdim = lat.size if len(lat.shape) == 1 else lat.shape[-2]
        # ## londim = lon.size if len(lon.shape) == 1 else lon.shape[-1]
        # ## data = np.empty((time.size, depthdim, latdim, londim), dtype=np.float32)
        data_list = []
        tidx = 0
        for tslice, fname in zip(timeslices, filenames):
            with FileBuffer(fname, dimensions) as filebuffer:
                depthsize = depth.size if len(depth.shape) == 1 else depth.shape[-3]
                latsize = lat.size if len(lat.shape) == 1 else lat.shape[-2]
                lonsize = lon.size if len(lon.shape) == 1 else lon.shape[-1]
                filebuffer.indslat = indices['lat'] if 'lat' in indices else range(latsize)
                filebuffer.indslon = indices['lon'] if 'lon' in indices else range(lonsize)
                filebuffer.indsdepth = indices['depth'] if 'depth' in indices else range(depthsize)
                for inds in [filebuffer.indslat, filebuffer.indslon, filebuffer.indsdepth]:
                    if not isinstance(inds, list):
                        raise RuntimeError('Indices sur field subsetting need to be a list')
                if 'data' in dimensions:
                    # If Field.from_netcdf is called directly, it may not have a 'data' dimension
                    # In that case, assume that 'name' is the data dimension
                    filebuffer.name = dimensions['data']
                else:
                    filebuffer.name = name

                # ## if len(filebuffer.dataset[filebuffer.name].shape) == 2:
                # ##     data[tidx:tidx+len(tslice), 0, :, :] = filebuffer.data[:, :]
                # ## elif len(filebuffer.dataset[filebuffer.name].shape) == 3:
                # ##     data[tidx:tidx+len(tslice), 0, :, :] = da.from_array(filebuffer.data, chunks=(1,1000,1000)) # filebuffer.data[:, :, :]
                # ##     print data[0,0,0,0], filebuffer.data[0,0,0]
                # ## else:
                # ##     data[tidx:tidx+len(tslice), :, :, :] = filebuffer.data[:, :, :, :]
                data_list.append(filebuffer.data)
            tidx += len(tslice)
        dataDask = da.concatenate(data_list, axis=0)
        data = dataDask  # dataDask[:].compute()
        # Time indexing after the fact only
        if 'time' in indices:
            time = time[indices['time']]
            data = data[indices['time'], :, :, :]
        if time.size == 1 and time[0] is None:
            time[0] = 0
        if len(lon.shape) == 1:
            if len(depth.shape) == 1:
                grid = RectilinearZGrid(lon, lat, depth, time, time_origin=time_origin, mesh=mesh)
            else:
                grid = RectilinearSGrid(lon, lat, depth, time, time_origin=time_origin, mesh=mesh)
        else:
            if len(depth.shape) == 1:
                grid = CurvilinearZGrid(lon, lat, depth, time, time_origin=time_origin, mesh=mesh)
            else:
                grid = CurvilinearSGrid(lon, lat, depth, time, time_origin=time_origin, mesh=mesh)
        if name in ['cosU', 'sinU', 'cosV', 'sinV']:
            allow_time_extrapolation = True
        return cls(name, data, grid=grid,
                   allow_time_extrapolation=allow_time_extrapolation, **kwargs)

    def getUV(self, time, x, y, z):
        fieldset = self.fieldset
        U = fieldset.U.eval(time, x, y, z, False)
        V = fieldset.V.eval(time, x, y, z, False)
        if fieldset.U.grid.gtype in [GridCode.RectilinearZGrid, GridCode.RectilinearSGrid]:
            zonal = U
            meridional = V
        else:
            cosU = fieldset.cosU.eval(time, x, y, z, False)
            sinU = fieldset.sinU.eval(time, x, y, z, False)
            cosV = fieldset.cosV.eval(time, x, y, z, False)
            sinV = fieldset.sinV.eval(time, x, y, z, False)
            zonal = U * cosU - V * sinV
            meridional = U * sinU + V * cosV
        zonal = fieldset.U.units.to_target(zonal, x, y, z)
        meridional = fieldset.V.units.to_target(meridional, x, y, z)
        return (zonal, meridional)

    def __getitem__(self, key):
        if self.name == 'UV':
            return self.getUV(*key)
        return self.eval(*key)

    def gradient(self, timerange=None, lonrange=None, latrange=None, name=None):
        """Method to create gradients of Field"""
        if name is None:
            name = 'd' + self.name

        if timerange is None:
            time_i = range(len(self.grid.time))
            time = self.grid.time
        else:
            time_i = range(np.where(self.grid.time >= timerange[0])[0][0], np.where(self.grid.time <= timerange[1])[0][-1]+1)
            time = self.grid.time[time_i]
        if lonrange is None:
            lon_i = range(len(self.grid.lon))
            lon = self.grid.lon
        else:
            lon_i = range(np.where(self.grid.lon >= lonrange[0])[0][0], np.where(self.grid.lon <= lonrange[1])[0][-1]+1)
            lon = self.grid.lon[lon_i]
        if latrange is None:
            lat_i = range(len(self.grid.lat))
            lat = self.grid.lat
        else:
            lat_i = range(np.where(self.grid.lat >= latrange[0])[0][0], np.where(self.grid.lat <= latrange[1])[0][-1]+1)
            lat = self.grid.lat[lat_i]

        dVdx = np.zeros(shape=(time.size, lat.size, lon.size), dtype=np.float32)
        dVdy = np.zeros(shape=(time.size, lat.size, lon.size), dtype=np.float32)
        for t in np.nditer(np.int32(time_i)):
            grad = CentralDifferences(np.transpose(self.data[t, :, :][np.ix_(lat_i, lon_i)]), lat, lon)
            dVdx[t, :, :] = np.array(np.transpose(grad[0]))
            dVdy[t, :, :] = np.array(np.transpose(grad[1]))

        return([Field(name + '_dx', dVdx, lon=lon, lat=lat, depth=self.grid.depth, time=time,
                      interp_method=self.interp_method, allow_time_extrapolation=self.allow_time_extrapolation),
                Field(name + '_dy', dVdy, lon=lon, lat=lat, depth=self.grid.depth, time=time,
                      interp_method=self.interp_method, allow_time_extrapolation=self.allow_time_extrapolation)])

    def interpolator2D_scipy(self, t_idx, z_idx=None):
        """Provide a SciPy interpolator for spatial interpolation

        Note that the interpolator is configured to return NaN for
        out-of-bounds coordinates.
        """
        if z_idx is None:
            data = self.data[t_idx, :]
        else:
            data = self.data[t_idx, z_idx, :]
        return RegularGridInterpolator((self.grid.lat, self.grid.lon), data,
                                       bounds_error=False, fill_value=np.nan,
                                       method=self.interp_method)

    def interpolator3D_rectilinear_z(self, idx, z, y, x):
        """Scipy implementation of 3D interpolation, by first interpolating
        in horizontal, then in the vertical"""

        zdx = self.depth_index(z, y, x)
        f0 = self.interpolator2D_scipy(idx, z_idx=zdx)((y, x))
        f1 = self.interpolator2D_scipy(idx, z_idx=zdx + 1)((y, x))
        z0 = self.grid.depth[zdx]
        z1 = self.grid.depth[zdx + 1]
        if z < z0 or z > z1:
            raise FieldSamplingError(x, y, z, field=self)
        if self.interp_method is 'nearest':
            return f0 if z - z0 < z1 - z else f1
        elif self.interp_method is 'linear':
            return f0 + (f1 - f0) * ((z - z0) / (z1 - z0))
        else:
            raise RuntimeError(self.interp_method+"is not implemented for 3D grids")

    def search_indices_vertical_z(self, z):
        grid = self.grid
        z = np.float32(z)
        depth_index = grid.depth <= z
        if z >= grid.depth[-1]:
            zi = len(grid.depth) - 2
        else:
            zi = depth_index.argmin() - 1 if z >= grid.depth[0] else 0
        zeta = (z-grid.depth[zi]) / (grid.depth[zi+1]-grid.depth[zi])
        return (zi, zeta)

    def search_indices_vertical_s(self, x, y, z, xi, yi, xsi, eta, tidx, time):
        grid = self.grid
        if grid.z4d:
            if tidx == len(grid.time)-1:
                depth_vector = (1-xsi)*(1-eta) * grid.depth[xi, yi, :, -1] + \
                    xsi*(1-eta) * grid.depth[xi+1, yi, :, -1] + \
                    xsi*eta * grid.depth[xi+1, yi+1, :, -1] + \
                    (1-xsi)*eta * grid.depth[xi, yi+1, :, -1]
            else:
                dv2 = (1-xsi)*(1-eta) * grid.depth[xi, yi, :, tidx:tidx+2] + \
                    xsi*(1-eta) * grid.depth[xi+1, yi, :, tidx:tidx+2] + \
                    xsi*eta * grid.depth[xi+1, yi+1, :, tidx:tidx+2] + \
                    (1-xsi)*eta * grid.depth[xi, yi+1, :, tidx:tidx+2]
                t0 = grid.time[tidx]
                t1 = grid.time[tidx + 1]
                depth_vector = dv2[:, 0] + (dv2[:, 1]-dv2[:, 0]) * (time - t0) / (t1 - t0)
        else:
            depth_vector = (1-xsi)*(1-eta) * grid.depth[xi, yi, :] + \
                xsi*(1-eta) * grid.depth[xi+1, yi, :] + \
                xsi*eta * grid.depth[xi+1, yi+1, :] + \
                (1-xsi)*eta * grid.depth[xi, yi+1, :]
        z = np.float32(z)
        depth_index = depth_vector <= z
        if z >= depth_vector[-1]:
            zi = len(depth_vector) - 2
        else:
            zi = depth_index.argmin() - 1 if z >= depth_vector[0] else 0
        if z < depth_vector[zi] or z > depth_vector[zi+1]:
            raise FieldSamplingError(x, y, z, field=self)
        zeta = (z - depth_vector[zi]) / (depth_vector[zi+1]-depth_vector[zi])
        return (zi, zeta)

    def fix_i_index(self, xi, dim, sphere_mesh):
        if xi < 0:
            if sphere_mesh:
                xi = dim-2
            else:
                xi = 0
        if xi > dim-2:
            if sphere_mesh:
                xi = 0
            else:
                xi = dim-2
        return xi

    def search_indices_rectilinear(self, x, y, z, tidx=-1, time=-1):
        grid = self.grid
        xi = yi = -1
        lon_index = grid.lon <= x

        if grid.mesh is not 'spherical':
            if x < grid.lon[0] or x > grid.lon[-1]:
                raise FieldSamplingError(x, y, z, field=self)
            lon_index = grid.lon <= x
            if lon_index.all():
                xi = len(grid.lon) - 2
            else:
                xi = lon_index.argmin() - 1 if lon_index.any() else 0
            xsi = (x-grid.lon[xi]) / (grid.lon[xi+1]-grid.lon[xi])
        else:
            lon_fixed = grid.lon
            lon_fixed = np.where(lon_fixed - x > 180., lon_fixed - 360, lon_fixed)
            lon_fixed = np.where(x - lon_fixed > 180., lon_fixed + 360, lon_fixed)
            if x < lon_fixed[0] or x > lon_fixed[-1]:
                raise FieldSamplingError(x, y, z, field=self)
            lon_index = lon_fixed <= x
            if lon_index.all():
                xi = len(lon_fixed) - 2
            else:
                xi = lon_index.argmin() - 1 if lon_index.any() else 0
            xsi = (x-lon_fixed[xi]) / (lon_fixed[xi+1]-lon_fixed[xi])

        if y < grid.lat[0] or y > grid.lat[-1]:
            raise FieldSamplingError(x, y, z, field=self)
        lat_index = grid.lat <= y
        if lat_index.all():
            yi = len(grid.lat) - 2
        else:
            yi = lat_index.argmin() - 1 if lat_index.any() else 0

        eta = (y-grid.lat[yi]) / (grid.lat[yi+1]-grid.lat[yi])

        if grid.zdim > 1:
            if grid.gtype == GridCode.RectilinearZGrid:
                # Never passes here, because in this case, we work with scipy
                (zi, zeta) = self.search_indices_vertical_z(z)
            elif grid.gtype == GridCode.RectilinearSGrid:
                (zi, zeta) = self.search_indices_vertical_s(x, y, z, xi, yi, xsi, eta, tidx, time)
        else:
            zi = 0
            zeta = 0

        assert(xsi >= 0 and xsi <= 1)
        assert(eta >= 0 and eta <= 1)
        assert(zeta >= 0 and zeta <= 1)

        return (xsi, eta, zeta, xi, yi, zi)

    def search_indices_curvilinear(self, x, y, z, xi, yi, tidx=-1, time=-1):
        xsi = eta = -1
        grid = self.grid
        invA = np.array([[1, 0, 0, 0],
                         [-1, 1, 0, 0],
                         [-1, 0, 0, 1],
                         [1, -1, 1, -1]])
        maxIterSearch = 1e6
        it = 0
        while xsi < 0 or xsi > 1 or eta < 0 or eta > 1:
            px = np.array([grid.lon[yi, xi], grid.lon[yi, xi+1], grid.lon[yi+1, xi+1], grid.lon[yi+1, xi]])
            if grid.mesh == 'spherical':
                px = np.where(px - x > 180, px-360, px)
                px = np.where(-px + x > 180, px+360, px)
            py = np.array([grid.lat[yi, xi], grid.lat[yi, xi+1], grid.lat[yi+1, xi+1], grid.lat[yi+1, xi]])
            a = np.dot(invA, px)
            b = np.dot(invA, py)

            aa = a[3]*b[2] - a[2]*b[3]
            if abs(aa) < 1e-12:  # Rectilinear cell, or quasi
                xsi = ((x-px[0]) / (px[1]-px[0])
                       + (x-px[3]) / (px[2]-px[3])) * .5
                eta = ((y-grid.lat[yi, xi]) / (grid.lat[yi+1, xi]-grid.lat[yi, xi])
                       + (y-grid.lat[yi, xi+1]) / (grid.lat[yi+1, xi+1]-grid.lat[yi, xi+1])) * .5
            else:
                bb = a[3]*b[0] - a[0]*b[3] + a[1]*b[2] - a[2]*b[1] + x*b[3] - y*a[3]
                cc = a[1]*b[0] - a[0]*b[1] + x*b[1] - y*a[1]
                det2 = bb*bb-4*aa*cc
                if det2 > 0:  # so, if det is nan we keep the xsi, eta from previous iter
                    det = np.sqrt(det2)
                    eta = (-bb+det)/(2*aa)
                    xsi = (x-a[0]-a[2]*eta) / (a[1]+a[3]*eta)
            if xsi < 0 and eta < 0 and xi == 0 and yi == 0:
                raise FieldSamplingError(x, y, 0, field=self)
            if xsi > 1 and eta > 1 and xi == grid.xdim-1 and yi == grid.ydim-1:
                raise FieldSamplingError(x, y, 0, field=self)
            if xsi < 0:
                xi -= 1
            elif xsi > 1:
                xi += 1
            if eta < 0:
                yi -= 1
            elif eta > 1:
                yi += 1
            xi = self.fix_i_index(xi, grid.xdim, grid.mesh == 'spherical')
            yi = self.fix_i_index(yi, grid.ydim, False)
            it += 1
            if it > maxIterSearch:
                print('Correct cell not found after %d iterations' % maxIterSearch)
                raise FieldSamplingError(x, y, 0, field=self)

        if grid.zdim > 1:
            if grid.gtype == GridCode.CurvilinearZGrid:
                (zi, zeta) = self.search_indices_vertical_z(z)
            elif grid.gtype == GridCode.CurvilinearSGrid:
                (zi, zeta) = self.search_indices_vertical_s(x, y, z, xi, yi, xsi, eta, tidx, time)
        else:
            zi = 0
            zeta = 0

        assert(xsi >= 0 and xsi <= 1)
        assert(eta >= 0 and eta <= 1)
        assert(zeta >= 0 and zeta <= 1)

        return (xsi, eta, zeta, xi, yi, zi)

    def search_indices(self, x, y, z, xi, yi, tidx=-1, time=-1):
        if self.grid.gtype == GridCode.RectilinearSGrid:
            return self.search_indices_rectilinear(x, y, z, tidx, time)
        else:
            return self.search_indices_curvilinear(x, y, z, xi, yi, tidx, time)

    def interpolator2D(self, tidx, z, y, x):
        xi = 0
        yi = 0
        (xsi, eta, trash, xi, yi, trash) = self.search_indices(x, y, z, xi, yi)
        if self.interp_method is 'nearest':
            xii = xi if xsi <= .5 else xi+1
            yii = yi if eta <= .5 else yi+1
            return self.data[tidx, yii, xii]
        elif self.interp_method is 'linear':
            val = (1-xsi)*(1-eta) * self.data[tidx, yi, xi] + \
                xsi*(1-eta) * self.data[tidx, yi, xi+1] + \
                xsi*eta * self.data[tidx, yi+1, xi+1] + \
                (1-xsi)*eta * self.data[tidx, yi+1, xi]
            return val
        else:
            raise RuntimeError(self.interp_method+"is not implemented for 3D grids")

    def interpolator3D(self, tidx, z, y, x, time):
        xi = int(self.grid.xdim / 2)
        yi = int(self.grid.ydim / 2)
        (xsi, eta, zeta, xi, yi, zi) = self.search_indices(x, y, z, xi, yi, tidx, time)
        if self.interp_method is 'nearest':
            xii = xi if xsi <= .5 else xi+1
            yii = yi if eta <= .5 else yi+1
            zii = zi if zeta <= .5 else zi+1
            return self.data[tidx, zii, yii, xii]
        elif self.interp_method is 'linear':
            data = self.data[tidx, zi, :, :].transpose()
            f0 = (1-xsi)*(1-eta) * data[xi, yi] + \
                xsi*(1-eta) * data[xi+1, yi] + \
                xsi*eta * data[xi+1, yi+1] + \
                    (1-xsi)*eta * data[xi, yi+1]
            data = self.data[tidx, zi+1, :, :].transpose()
            f1 = (1-xsi)*(1-eta) * data[xi, yi] + \
                xsi*(1-eta) * data[xi+1, yi] + \
                xsi*eta * data[xi+1, yi+1] + \
                (1-xsi)*eta * data[xi, yi+1]
            return (1-zeta) * f0 + zeta * f1
        else:
            raise RuntimeError(self.interp_method+"is not implemented for 3D grids")

    def temporal_interpolate_fullfield(self, tidx, time):
        """Calculate the data of a field between two snapshots,
        using linear interpolation

        :param tidx: Index in time array associated with time (via :func:`time_index`)
        :param time: Time to interpolate to

        :rtype: Linearly interpolated field"""
        t0 = self.grid.time[tidx]
        t1 = self.grid.time[tidx+1]
        f0 = self.data[tidx, :]
        f1 = self.data[tidx+1, :]
        return f0 + (f1 - f0) * ((time - t0) / (t1 - t0))

    def spatial_interpolation(self, tidx, z, y, x, time):
        """Interpolate horizontal field values using a SciPy interpolator"""

        if self.grid.gtype is GridCode.RectilinearZGrid:  # The only case where we use scipy interpolation
            if self.grid.zdim == 1:
                val = self.interpolator2D_scipy(tidx)((y, x))
            else:
                val = self.interpolator3D_rectilinear_z(tidx, z, y, x)
        elif self.grid.gtype in [GridCode.RectilinearSGrid, GridCode.CurvilinearZGrid, GridCode.CurvilinearSGrid]:
            if self.grid.zdim == 1:
                val = self.interpolator2D(tidx, z, y, x)
            else:
                val = self.interpolator3D(tidx, z, y, x, time)
        else:
            raise RuntimeError("Only RectilinearZGrid, RectilinearSGrid and CRectilinearGrid grids are currently implemented")
        if np.isnan(val):
            # Detect Out-of-bounds sampling and raise exception
            raise FieldSamplingError(x, y, z, field=self)
        else:
            return val

    def time_index(self, time):
        """Find the index in the time array associated with a given time

        Note that we normalize to either the first or the last index
        if the sampled value is outside the time value range.
        """
        if not self.time_periodic and not self.allow_time_extrapolation and (time < self.grid.time[0] or time > self.grid.time[-1]):
            raise TimeExtrapolationError(time, field=self)
        time_index = self.grid.time <= time
        if self.time_periodic:
            if time_index.all() or np.logical_not(time_index).all():
                periods = math.floor((time-self.grid.time[0])/(self.grid.time[-1]-self.grid.time[0]))
                time -= periods*(self.grid.time[-1]-self.grid.time[0])
                time_index = self.grid.time <= time
                ti = time_index.argmin() - 1 if time_index.any() else 0
                return (ti, periods)
            return (time_index.argmin() - 1 if time_index.any() else 0, 0)
        if time_index.all():
            # If given time > last known field time, use
            # the last field frame without interpolation
            return (len(self.grid.time) - 1, 0)
        else:
            return (time_index.argmin() - 1 if time_index.any() else 0, 0)

    def depth_index(self, depth, lat, lon):
        """Find the index in the depth array associated with a given depth"""
        if depth > self.grid.depth[-1]:
            raise FieldSamplingError(lon, lat, depth, field=self)
        depth_index = self.grid.depth <= depth
        if depth_index.all():
            # If given depth == largest field depth, use the second-last
            # field depth (as zidx+1 needed in interpolation)
            return len(self.grid.depth) - 2
        else:
            return depth_index.argmin() - 1 if depth_index.any() else 0

    def eval(self, time, x, y, z, applyConversion=True):
        """Interpolate field values in space and time.

        We interpolate linearly in time and apply implicit unit
        conversion to the result. Note that we defer to
        scipy.interpolate to perform spatial interpolation.
        """
        (t_idx, periods) = self.time_index(time)
        time -= periods*(self.grid.time[-1]-self.grid.time[0])
        if t_idx < self.grid.tdim-1 and time > self.grid.time[t_idx]:
            f0 = self.spatial_interpolation(t_idx, z, y, x, time)
            f1 = self.spatial_interpolation(t_idx + 1, z, y, x, time)
            t0 = self.grid.time[t_idx]
            t1 = self.grid.time[t_idx + 1]
            value = f0 + (f1 - f0) * ((time - t0) / (t1 - t0))
        else:
            # Skip temporal interpolation if time is outside
            # of the defined time range or if we have hit an
            # excat value in the time array.
            value = self.spatial_interpolation(t_idx, z, y, x, self.grid.time[t_idx-1])

        if applyConversion:
            return self.units.to_target(value, x, y, z)
        else:
            return value

    def ccode_evalUV(self, varU, varV, t, x, y, z):
        # Casting interp_methd to int as easier to pass on in C-code

        gridset = self.fieldset.gridset
        uiGrid = -1
        viGrid = -1
        if self.fieldset.U.grid.gtype in [GridCode.RectilinearZGrid, GridCode.RectilinearSGrid]:
            for i, g in enumerate(gridset.grids):
                if min(uiGrid, viGrid) > -1:
                    break
                if g is self.fieldset.U.grid:
                    uiGrid = i
                if g is self.fieldset.V.grid:
                    viGrid = i
            return "temporal_interpolationUV(%s, %s, %s, %s, U, V, particle->CGridIndexSet, %s, %s, &%s, &%s, %s)" \
                % (x, y, z, t,
                   uiGrid, viGrid, varU, varV, self.fieldset.U.interp_method.upper())
        else:
            cosuiGrid = -1
            sinuiGrid = -1
            cosviGrid = -1
            sinviGrid = -1
            for i, g in enumerate(gridset.grids):
                if min(uiGrid, viGrid, cosuiGrid, sinuiGrid, cosviGrid, sinviGrid) > -1:
                    break
                if g is self.fieldset.U.grid:
                    uiGrid = i
                if g is self.fieldset.V.grid:
                    viGrid = i
                if g is self.fieldset.cosU.grid:
                    cosuiGrid = i
                if g is self.fieldset.sinU.grid:
                    sinuiGrid = i
                if g is self.fieldset.cosV.grid:
                    cosviGrid = i
                if g is self.fieldset.sinV.grid:
                    sinviGrid = i
            return "temporal_interpolationUVrotation(%s, %s, %s, %s, U, V, cosU, sinU, cosV, sinV, particle->CGridIndexSet, %s, %s, %s, %s, %s, %s, &%s, &%s, %s)" \
                % (x, y, z, t,
                   uiGrid, viGrid, cosuiGrid, sinuiGrid, cosviGrid, sinviGrid,
                   varU, varV, self.fieldset.U.interp_method.upper())

    def ccode_eval(self, var, t, x, y, z):
        # Casting interp_methd to int as easier to pass on in C-code
        gridset = self.fieldset.gridset
        iGrid = -1
        for i, g in enumerate(gridset.grids):
            if g is self.grid:
                iGrid = i
                break
        return "temporal_interpolation(%s, %s, %s, %s, %s, %s, %s, &%s, %s)" \
            % (x, y, z, t, self.name, "particle->CGridIndexSet", iGrid, var,
               self.interp_method.upper())

    def ccode_convert(self, _, x, y, z):
        return self.units.ccode_to_target(x, y, z)

    @property
    def ctypes_struct(self):
        """Returns a ctypes struct object containing all relevant
        pointers and sizes for this field."""

        # Ctypes struct corresponding to the type definition in parcels.h
        class CField(Structure):
            _fields_ = [('xdim', c_int), ('ydim', c_int), ('zdim', c_int),
                        ('tdim', c_int),
                        ('allow_time_extrapolation', c_int),
                        ('time_periodic', c_int),
                        ('data', POINTER(POINTER(c_float))),
                        ('grid', POINTER(CGrid))]

        # Create and populate the c-struct object
        allow_time_extrapolation = 1 if self.allow_time_extrapolation else 0
        time_periodic = 1 if self.time_periodic else 0
        cstruct = CField(self.grid.xdim, self.grid.ydim, self.grid.zdim,
                         self.grid.tdim, allow_time_extrapolation, time_periodic,
                         self.data.ctypes.data_as(POINTER(POINTER(c_float))),
                         pointer(self.grid.ctypes_struct))
        return cstruct

    def show(self, with_particles=False, animation=False, show_time=None, vmin=None, vmax=None):
        """Method to 'show' a :class:`Field` using matplotlib

        :param with_particles: Boolean whether particles are also plotted on Field
        :param animation: Boolean whether result is a single plot, or an animation
        :param show_time: Time at which to show the Field (only in single-plot mode)
        :param vmin: minimum colour scale (only in single-plot mode)
        :param vmax: maximum colour scale (only in single-plot mode)
        """
        try:
            import matplotlib.pyplot as plt
            import matplotlib.animation as animation_plt
            from matplotlib import rc
        except:
            logger.info("Visualisation is not possible. Matplotlib not found.")
            return

        if with_particles or (not animation):
            show_time = self.grid.time[0] if show_time is None else show_time
            (idx, periods) = self.time_index(show_time)
            show_time -= periods*(self.grid.time[-1]-self.grid.time[0])
            if self.grid.time.size > 1:
                data = np.squeeze(self.temporal_interpolate_fullfield(idx, show_time))
            else:
                data = np.squeeze(self.data)

            vmin = data.min() if vmin is None else vmin
            vmax = data.max() if vmax is None else vmax
            cs = plt.contourf(self.grid.lon, self.grid.lat, data,
                              levels=np.linspace(vmin, vmax, 256))
            cs.cmap.set_over('k')
            cs.cmap.set_under('w')
            cs.set_clim(vmin, vmax)
            plt.colorbar(cs)
            if not with_particles:
                plt.show()
        else:
            fig = plt.figure()
            ax = plt.axes(xlim=(self.grid.lon[0], self.grid.lon[-1]), ylim=(self.grid.lat[0], self.grid.lat[-1]))

            def animate(i):
                data = np.squeeze(self.data[i, :, :])
                cont = ax.contourf(self.grid.lon, self.grid.lat, data,
                                   levels=np.linspace(data.min(), data.max(), 256))
                return cont

            rc('animation', html='html5')
            anim = animation_plt.FuncAnimation(fig, animate, frames=np.arange(1, self.data.shape[0]),
                                               interval=100, blit=False)
            plt.close()
            return anim

    def add_periodic_halo(self, zonal, meridional, halosize=5):
        """Add a 'halo' to all Fields in a FieldSet, through extending the Field (and lon/lat)
        by copying a small portion of the field on one side of the domain to the other.
        Before adding a periodic halo to the Field, it has to be added to the Grid on which the Field depends

        :param zonal: Create a halo in zonal direction (boolean)
        :param meridional: Create a halo in meridional direction (boolean)
        :param halosize: size of the halo (in grid points). Default is 5 grid points
        """
        if self.name == 'UV':
            return
        if zonal:
            if len(self.data.shape) is 3:
                self.data = np.concatenate((self.data[:, :, -halosize:], self.data,
                                            self.data[:, :, 0:halosize]), axis=len(self.data.shape)-1)
                assert self.data.shape[2] == self.grid.xdim
            else:
                self.data = np.concatenate((self.data[:, :, :, -halosize:], self.data,
                                            self.data[:, :, :, 0:halosize]), axis=len(self.data.shape) - 1)
                assert self.data.shape[3] == self.grid.xdim
            self.lon = self.grid.lon
            self.lat = self.grid.lat
        if meridional:
            if len(self.data.shape) is 3:
                self.data = np.concatenate((self.data[:, -halosize:, :], self.data,
                                            self.data[:, 0:halosize, :]), axis=len(self.data.shape)-2)
                assert self.data.shape[1] == self.grid.ydim
            else:
                self.data = np.concatenate((self.data[:, :, -halosize:, :], self.data,
                                            self.data[:, :, 0:halosize, :]), axis=len(self.data.shape) - 2)
                assert self.data.shape[2] == self.grid.ydim
            self.lat = self.grid.lat

    def write(self, filename, varname=None):
        """Write a :class:`Field` to a netcdf file

        :param filename: Basename of the file
        :param varname: Name of the field, to be appended to the filename"""
        if self.name == 'UV':
            return
        filepath = str(path.local('%s%s.nc' % (filename, self.name)))
        if varname is None:
            varname = self.name
        # Derive name of 'depth' variable for NEMO convention
        vname_depth = 'depth%s' % self.name.lower()

        # Create DataArray objects for file I/O
        t, d, x, y = (self.grid.time.size, self.grid.depth.size,
                      self.grid.lon.size, self.grid.lat.size)
        nav_lon = xarray.DataArray(self.grid.lon + np.zeros((y, x), dtype=np.float32),
                                   coords=[('y', self.grid.lat), ('x', self.grid.lon)])
        nav_lat = xarray.DataArray(self.grid.lat.reshape(y, 1) + np.zeros(x, dtype=np.float32),
                                   coords=[('y', self.grid.lat), ('x', self.grid.lon)])
        vardata = xarray.DataArray(self.data.reshape((t, d, y, x)),
                                   coords=[('time_counter', self.grid.time),
                                           (vname_depth, self.grid.depth),
                                           ('y', self.grid.lat), ('x', self.grid.lon)])
        # Create xarray Dataset and output to netCDF format
        dset = xarray.Dataset({varname: vardata}, coords={'nav_lon': nav_lon,
                                                          'nav_lat': nav_lat,
                                                          vname_depth: self.grid.depth})
        dset.to_netcdf(filepath)

    def advancetime(self, field_new, advanceForward):
        if advanceForward == 1:  # forward in time, so appending at end
            self.data = np.concatenate((self.data[1:, :, :], field_new.data[:, :, :]), 0)
            self.time = self.grid.time
        else:  # backward in time, so prepending at start
            self.data = np.concatenate((field_new.data[:, :, :], self.data[:-1, :, :]), 0)
            self.time = self.grid.time


class FileBuffer(object):
    """ Class that encapsulates and manages deferred access to file data. """

    def __init__(self, filename, dimensions):
        self.filename = filename
        self.dimensions = dimensions  # Dict with dimension keyes for file data
        self.dataset = None

    def __enter__(self):
        self.dataset = Dataset(str(self.filename), 'r', format="NETCDF4")
        return self

    def __exit__(self, type, value, traceback):
        self.dataset.close()

    def subset(self, dim, dimname, indices):
        if len(dim.shape) == 1:  # RectilinearZGrid
            inds = indices[dimname] if dimname in indices else range(dim.size)
            dim_inds = dim[inds]
        else:
            inds_lon = indices['lon'] if 'lon' in indices else range(dim.shape[-1])
            inds_lat = indices['lat'] if 'lat' in indices else range(dim.shape[-2])
            if len(dim.shape) == 2:  # CurvilinearGrid
                dim_inds = dim[inds_lat, inds_lon]
            elif len(dim.shape) == 3:  # SGrid
                inds_depth = indices['depth'] if 'depth' in indices else range(dim.shape[0])
                dim_inds = dim[inds_depth, inds_lat, inds_lon]
            elif len(dim.shape) == 4:  # SGrid
                inds_depth = indices['depth'] if 'depth' in indices else range(dim.shape[0])
                dim_inds = dim[:, inds_depth, inds_lat, inds_lon]
        return dim_inds

    def read_lonlat(self, indices):
        lon = self.dataset[self.dimensions['lon']]
        lat = self.dataset[self.dimensions['lat']]
        if len(lon.shape) > 1:
            londim = lon.shape[0]
            latdim = lat.shape[1]
            if np.allclose(lon[0, :], lon[int(londim/2), :]) and np.allclose(lat[:, 0], lat[:, int(latdim/2)]):
                lon = lon[0, :]
                lat = lat[:, 0]
        lon_subset = self.subset(lon, 'lon', indices)
        lat_subset = self.subset(lat, 'lat', indices)
        return lon_subset, lat_subset

    def read_depth(self, indices):
        if 'depth' in self.dimensions:
            depth = self.dataset[self.dimensions['depth']]
            return self.subset(depth, 'depth', indices)
        else:
            return np.zeros(1)

    @property
    def data(self):
        if len(self.dataset[self.name].shape) == 2:
            data = self.dataset[self.name][self.indslat, self.indslon]
        elif len(self.dataset[self.name].shape) == 3:
            data = self.dataset[self.name][:, self.indslat, self.indslon]
        else:
            data = self.dataset[self.name][:, self.indsdepth, self.indslat, self.indslon]

        if np.ma.is_masked(data):  # convert masked array to ndarray
            data = np.ma.filled(data, np.nan)
        if len(self.dataset[self.name].shape) == 2:
            return da.from_array(data, chunks=data.shape)
        else:
            return da.from_array(data, chunks=sum(((1,), data.shape[1:]), ()))

    @property
    def time(self):
        if self.time_units is not None:
            dt = num2date(self.dataset[self.dimensions['time']][:],
                          self.time_units, self.calendar)
            offset = num2date(0, self.time_units, self.calendar)
            if type(offset) is datetime:
                dt -= offset
            else:
                # num2date in some cases returns a 'phony' datetime. In that case,
                # parse it as a string.
                # See http://unidata.github.io/netcdf4-python/#netCDF4.num2date
                dt -= parse(str(offset))
            return list(map(timedelta.total_seconds, dt))
        else:
            try:
                return self.dataset[self.dimensions['time']][:]
            except:
                return [None]

    @property
    def time_units(self):
        """ Derive time_units if the time dimension has units """
        try:
            return self.dataset[self.dimensions['time']].units
        except:
            try:
                return self.dataset[self.dimensions['time']].Unit
            except:
                return None

    @property
    def calendar(self):
        """ Derive calendar if the time dimension has calendar """
        try:
            calendar = self.dataset[self.dimensions['time']].calendar
            if calendar is ('proleptic_gregorian' or 'standard' or 'gregorian'):
                return calendar
            else:
                # Other calendars means the time can't be converted to datetime object
                # See http://unidata.github.io/netcdf4-python/#netCDF4.num2date
                return 'standard'
        except:
            return 'standard'
