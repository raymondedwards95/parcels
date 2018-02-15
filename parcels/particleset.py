from parcels.kernel import Kernel
from parcels.field import Field, UnitConverter
from parcels.particle import JITParticle
from parcels.compiler import GNUCompiler
from parcels.kernels.advection import AdvectionRK4
from parcels.particlefile import ParticleFile
from parcels.loggers import logger
import numpy as np
import bisect
from collections import Iterable
from datetime import timedelta as delta
from datetime import datetime

__all__ = ['ParticleSet']


def nearest_index(array, value):
    """returns index of the nearest value in array using O(log n) bisection method"""
    y = bisect.bisect(array, value)
    if y == len(array):
        return y-1
    elif(abs(array[y-1] - value) < abs(array[y] - value)):
        return y-1
    else:
        return y


class ParticleSet(object):
    """Container class for storing particle and executing kernel over them.

    Please note that this currently only supports fixed size particle sets.

    :param fieldset: :mod:`parcels.fieldset.FieldSet` object from which to sample velocity
    :param pclass: Optional :mod:`parcels.particle.JITParticle` or
                 :mod:`parcels.particle.ScipyParticle` object that defines custom particle
    :param lon: List of initial longitude values for particles
    :param lat: List of initial latitude values for particles
    :param depth: Optional list of initial depth values for particles. Default is 0m
    :param time: Optional list of initial time values for particles. Default is fieldset.U.grid.time[0]
    :param repeatdt: Optional interval (in seconds) on which to repeat the release of the ParticleSet
    """

    def __init__(self, fieldset, pclass=JITParticle, lon=[], lat=[], depth=None, time=None, repeatdt=None):
        self.fieldset = fieldset
        self.fieldset.check_complete()

        def convert_to_list(var):
            # Convert numpy arrays and single integers/floats to one-dimensional lists
            if isinstance(var, (int, float)):
                return [var]
            elif isinstance(var, np.ndarray):
                return var.flatten()
            return var

        lon = convert_to_list(lon)
        lat = convert_to_list(lat)
        depth = np.ones(len(lon)) * fieldset.U.grid.depth[0] if depth is None else depth
        depth = convert_to_list(depth)
        assert len(lon) == len(lat) and len(lon) == len(depth)

        time = time.tolist() if isinstance(time, np.ndarray) else time
        time = [time] * len(lat) if not isinstance(time, list) else time
        time = [t.total_seconds() if isinstance(t, delta) else t for t in time]
        time = [(t - fieldset.U.grid.time_origin).total_seconds() if isinstance(t, datetime) else t for t in time]

        assert len(lon) == len(time)

        self.repeatdt = repeatdt.total_seconds() if isinstance(repeatdt, delta) else repeatdt
        if self.repeatdt:
            if self.repeatdt <= 0:
                raise('Repeatdt should be > 0')
            if time[0] and not np.allclose(time, time[0]):
                raise ('All Particle.time should be the same when repeatdt is not None')
            self.repeat_starttime = time[0]
            self.repeatlon = lon
            self.repeatlat = lat
            self.repeatdepth = depth
            self.repeatpclass = pclass

        size = len(lon)
        self.particles = np.empty(size, dtype=pclass)
        self.ptype = pclass.getPType()
        self.kernel = None
        self.time_origin = fieldset.U.grid.time_origin

        if self.ptype.uses_jit:
            # Allocate underlying data for C-allocated particles
            self._particle_data = np.empty(size, dtype=self.ptype.dtype)

            def cptr(i):
                return self._particle_data[i]
        else:
            def cptr(i):
                return None

        if lon is not None and lat is not None:
            # Initialise from lists of lon/lat coordinates
            assert(size == len(lon) and size == len(lat))

            for i in range(size):
                self.particles[i] = pclass(lon[i], lat[i], fieldset=fieldset, depth=depth[i], cptr=cptr(i), time=time[i])
        else:
            raise ValueError("Latitude and longitude required for generating ParticleSet")

    @classmethod
    def from_list(cls, fieldset, pclass, lon, lat, depth=None, time=None, repeatdt=None):
        """Initialise the ParticleSet from lists of lon and lat

        :param fieldset: :mod:`parcels.fieldset.FieldSet` object from which to sample velocity
        :param pclass: mod:`parcels.particle.JITParticle` or :mod:`parcels.particle.ScipyParticle`
                 object that defines custom particle
        :param lon: List of initial longitude values for particles
        :param lat: List of initial latitude values for particles
        :param depth: Optional list of initial depth values for particles. Default is 0m
        :param time: Optional list of start time values for particles. Default is fieldset.U.time[0]
        :param repeatdt: Optional interval (in seconds) on which to repeat the release of the ParticleSet
       """
        return cls(fieldset=fieldset, pclass=pclass, lon=lon, lat=lat, depth=depth, time=time, repeatdt=repeatdt)

    @classmethod
    def from_line(cls, fieldset, pclass, start, finish, size, depth=None, time=None, repeatdt=None):
        """Initialise the ParticleSet from start/finish coordinates with equidistant spacing
        Note that this method uses simple numpy.linspace calls and does not take into account
        great circles, so may not be a exact on a globe

        :param fieldset: :mod:`parcels.fieldset.FieldSet` object from which to sample velocity
        :param pclass: mod:`parcels.particle.JITParticle` or :mod:`parcels.particle.ScipyParticle`
                 object that defines custom particle
        :param start: Starting point for initialisation of particles on a straight line.
        :param finish: End point for initialisation of particles on a straight line.
        :param size: Initial size of particle set
        :param depth: Optional list of initial depth values for particles. Default is 0m
        :param time: Optional start time value for particles. Default is fieldset.U.time[0]
        :param repeatdt: Optional interval (in seconds) on which to repeat the release of the ParticleSet
        """
        lon = np.linspace(start[0], finish[0], size, dtype=np.float32)
        lat = np.linspace(start[1], finish[1], size, dtype=np.float32)
        return cls(fieldset=fieldset, pclass=pclass, lon=lon, lat=lat, depth=depth, time=time, repeatdt=repeatdt)

    @classmethod
    def from_field(cls, fieldset, pclass, start_field, size, mode='monte_carlo', depth=None, time=None, repeatdt=None):
        """Initialise the ParticleSet randomly drawn according to distribution from a field

        :param fieldset: :mod:`parcels.fieldset.FieldSet` object from which to sample velocity
        :param pclass: mod:`parcels.particle.JITParticle` or :mod:`parcels.particle.ScipyParticle`
                 object that defines custom particle
        :param start_field: Field for initialising particles stochastically according to the presented density field.
        :param size: Initial size of particle set
        :param mode: Type of random sampling. Currently only 'monte_carlo' is implemented
        :param depth: Optional list of initial depth values for particles. Default is 0m
        :param time: Optional start time value for particles. Default is fieldset.U.time[0]
        :param repeatdt: Optional interval (in seconds) on which to repeat the release of the ParticleSet
        """
        lonwidth = (start_field.grid.lon[1] - start_field.grid.lon[0]) / 2
        latwidth = (start_field.grid.lat[1] - start_field.grid.lat[0]) / 2

        def add_jitter(pos, width, min, max):
            value = pos + np.random.uniform(-width, width)
            while not (min <= value <= max):
                value = pos + np.random.uniform(-width, width)
            return value

        if mode == 'monte_carlo':
            p = np.reshape(start_field.data, (1, start_field.data.size))
            inds = np.random.choice(start_field.data.size, size, replace=True, p=p[0] / np.sum(p))
            lat, lon = np.unravel_index(inds, start_field.data[0, :, :].shape)
            lon = fieldset.U.grid.lon[lon]
            lat = fieldset.U.grid.lat[lat]
            for i in range(lon.size):
                lon[i] = add_jitter(lon[i], lonwidth, start_field.grid.lon[0], start_field.grid.lon[-1])
                lat[i] = add_jitter(lat[i], latwidth, start_field.grid.lat[0], start_field.grid.lat[-1])
        else:
            raise NotImplementedError('Mode %s not implemented. Please use "monte carlo" algorithm instead.' % mode)

        return cls(fieldset=fieldset, pclass=pclass, lon=lon, lat=lat, depth=depth, time=time, repeatdt=repeatdt)

    @property
    def size(self):
        return self.particles.size

    def __repr__(self):
        return "\n".join([str(p) for p in self])

    def __len__(self):
        return self.size

    def __getitem__(self, key):
        return self.particles[key]

    def __setitem__(self, key, value):
        self.particles[key] = value

    def __iadd__(self, particles):
        self.add(particles)
        return self

    def add(self, particles):
        """Method to add particles to the ParticleSet"""
        if isinstance(particles, ParticleSet):
            particles = particles.particles
        if not isinstance(particles, Iterable):
            particles = [particles]
        self.particles = np.append(self.particles, particles)
        if self.ptype.uses_jit:
            particles_data = [p._cptr for p in particles]
            self._particle_data = np.append(self._particle_data, particles_data)
            # Update C-pointer on particles
            for p, pdata in zip(self.particles, self._particle_data):
                p._cptr = pdata

    def remove(self, indices):
        """Method to remove particles from the ParticleSet, based on their `indices`"""
        if isinstance(indices, Iterable):
            particles = [self.particles[i] for i in indices]
        else:
            particles = self.particles[indices]
        self.particles = np.delete(self.particles, indices)
        if self.ptype.uses_jit:
            self._particle_data = np.delete(self._particle_data, indices)
            # Update C-pointer on particles
            for p, pdata in zip(self.particles, self._particle_data):
                p._cptr = pdata
        return particles

    def execute(self, pyfunc=AdvectionRK4, endtime=None, runtime=None, dt=1.,
                moviedt=None, recovery=None, output_file=None, movie_background_field=None):
        """Execute a given kernel function over the particle set for
        multiple timesteps. Optionally also provide sub-timestepping
        for particle output.

        :param pyfunc: Kernel function to execute. This can be the name of a
                       defined Python function or a :class:`parcels.kernel.Kernel` object.
                       Kernels can be concatenated using the + operator
        :param endtime: End time for the timestepping loop.
                        It is either a datetime object or a positive double.
        :param runtime: Length of the timestepping loop. Use instead of endtime.
                        It is either a timedelta object or a positive double.
        :param dt: Timestep interval to be passed to the kernel.
                   It is either a timedelta object or a double.
                   Use a negative value for a backward-in-time simulation.
        :param moviedt:  Interval for inner sub-timestepping (leap), which dictates
                         the update frequency of animation.
                         It is either a timedelta object or a positive double.
                         None value means no animation.
        :param output_file: :mod:`parcels.particlefile.ParticleFile` object for particle output
        :param recovery: Dictionary with additional `:mod:parcels.kernels.error`
                         recovery kernels to allow custom recovery behaviour in case of
                         kernel errors.
        :param movie_background_field: field plotted as background in the movie if moviedt is set.
                                       'vector' shows the velocity as a vector field.

        """

        # check if pyfunc has changed since last compile. If so, recompile
        if self.kernel is None or (self.kernel.pyfunc is not pyfunc and self.kernel is not pyfunc):
            # Generate and store Kernel
            if isinstance(pyfunc, Kernel):
                self.kernel = pyfunc
            else:
                self.kernel = self.Kernel(pyfunc)
            # Prepare JIT kernel execution
            if self.ptype.uses_jit:
                self.kernel.remove_lib()
                self.kernel.compile(compiler=GNUCompiler())
                self.kernel.load_lib()

        # Convert all time variables to seconds
        if isinstance(endtime, delta):
            raise RuntimeError('endtime must be either a datetime or a double')
        if isinstance(endtime, datetime):
            endtime = (endtime - self.time_origin).total_seconds()
        if isinstance(runtime, delta):
            runtime = runtime.total_seconds()
        if isinstance(dt, delta):
            dt = dt.total_seconds()
        outputdt = output_file.outputdt if output_file else np.infty
        if isinstance(outputdt, delta):
            outputdt = outputdt.total_seconds()
        if isinstance(moviedt, delta):
            moviedt = moviedt.total_seconds()

        assert runtime is None or runtime >= 0, 'runtime must be positive'
        assert outputdt is None or outputdt >= 0, 'outputdt must be positive'
        assert moviedt is None or moviedt >= 0, 'moviedt must be positive'

        # Set particle.time defaults based on sign of dt, if not set at ParticleSet construction
        for p in self:
            if np.isnan(p.time):
                p.time = self.fieldset.U.grid.time[0] if dt >= 0 else self.fieldset.U.grid.time[-1]

        # Derive _starttime and endtime from arguments or fieldset defaults
        if runtime is not None and endtime is not None:
            raise RuntimeError('Only one of (endtime, runtime) can be specified')
        _starttime = min([p.time for p in self]) if dt >= 0 else max([p.time for p in self])
        if self.repeatdt is not None and self.repeat_starttime is None:
            self.repeat_starttime = _starttime
        if runtime is not None:
            endtime = _starttime + runtime * np.sign(dt)
        elif endtime is None:
            endtime = self.fieldset.U.grid.time[-1] if dt >= 0 else self.fieldset.U.grid.time[0]

        if abs(endtime-_starttime) < 1e-5 or dt == 0 or runtime == 0:
            dt = 0
            runtime = 0
            endtime = _starttime
            logger.warning_once("dt or runtime are zero, or endtime is equal to Particle.time. "
                                "The kernels will be executed once, without incrementing time")

        # Initialise particle timestepping
        for p in self:
            p.dt = dt

        # First write output_file, because particles could have been added
        if output_file:
            output_file.write(self, _starttime)
        if moviedt:
            self.show(field=movie_background_field, show_time=_starttime)

        if moviedt is None:
            moviedt = np.infty
        time = _starttime
        if self.repeatdt:
            next_prelease = self.repeat_starttime + (abs(time - self.repeat_starttime) // self.repeatdt + 1) * self.repeatdt * np.sign(dt)
        else:
            next_prelease = np.infty * np.sign(dt)
        next_output = time + outputdt * np.sign(dt)
        next_movie = time + moviedt * np.sign(dt)
        next_input = self.fieldset.computeChunk(time, np.sign(dt))

        tol = 1e-12
        while (time < endtime and dt > 0) or (time > endtime and dt < 0) or dt == 0:
            if dt > 0:
                time = min(next_prelease, next_input, next_output, next_movie, endtime)
            else:
                time = max(next_prelease, next_input, next_output, next_movie, endtime)
            self.kernel.execute(self, endtime=time, dt=dt, recovery=recovery)
            if abs(time-next_prelease) < tol:
                self.add(ParticleSet(fieldset=self.fieldset, time=time, lon=self.repeatlon,
                                     lat=self.repeatlat, depth=self.repeatdepth,
                                     pclass=self.repeatpclass))
                next_prelease += self.repeatdt * np.sign(dt)
            if abs(time-next_input) < tol:
                next_input = self.fieldset.computeChunk(time, np.sign(dt))
            if abs(time-next_output) < tol:
                if output_file:
                    output_file.write(self, time)
                next_output += outputdt * np.sign(dt)
            if abs(time-next_movie) < tol:
                self.show(field=movie_background_field, show_time=time)
                next_movie += moviedt * np.sign(dt)
            if dt == 0:
                break

        if output_file:
            output_file.write(self, time)

    def show(self, particles=True, show_time=None, field=None, domain=None,
             land=False, vmin=None, vmax=None, savefile=None):
        """Method to 'show' a Parcels ParticleSet

        :param particles: Boolean whether to show particles
        :param show_time: Time at which to show the ParticleSet
        :param field: Field to plot under particles (either None, a Field object, or 'vector')
        :param domain: Four-vector (latN, latS, lonE, lonW) defining domain to show
        :param land: Boolean whether to show land (in field='vector' mode only)
        :param vmin: minimum colour scale (only in single-plot mode)
        :param vmax: maximum colour scale (only in single-plot mode)
        :param savefile: Name of a file to save the plot to
        """
        try:
            import matplotlib.pyplot as plt
        except:
            logger.info("Visualisation is not possible. Matplotlib not found.")
            return
        try:
            from mpl_toolkits.basemap import Basemap
        except:
            Basemap = None

        plon = np.array([p.lon for p in self])
        plat = np.array([p.lat for p in self])
        show_time = self[0].time if show_time is None else show_time
        if isinstance(show_time, datetime):
            show_time = (show_time - self.fieldset.U.grid.time_origin).total_seconds()
        if isinstance(show_time, delta):
            show_time = show_time.total_seconds()
        if np.isnan(show_time):
            show_time = self.fieldset.U.grid.time[0]
        if domain is not None:
            latN = nearest_index(self.fieldset.U.lat, domain[0])
            latS = nearest_index(self.fieldset.U.lat, domain[1])
            lonE = nearest_index(self.fieldset.U.lon, domain[2])
            lonW = nearest_index(self.fieldset.U.lon, domain[3])
        else:
            latN, latS, lonE, lonW = (-1, 0, -1, 0)
        if field is not 'vector':
            plt.ion()
            plt.clf()
            if particles:
                plt.plot(np.transpose(plon), np.transpose(plat), 'ko')
            if field is None:
                axes = plt.gca()
                axes.set_xlim([self.fieldset.U.lon[lonW], self.fieldset.U.lon[lonE]])
                axes.set_ylim([self.fieldset.U.lat[latS], self.fieldset.U.lat[latN]])
                namestr = ''
                time_origin = self.fieldset.U.grid.time_origin
            else:
                if not isinstance(field, Field):
                    field = getattr(self.fieldset, field)
                field.show(with_particles=True, show_time=show_time, vmin=vmin, vmax=vmax)
                namestr = field.name
                time_origin = field.grid.time_origin
            xlbl = 'Zonal distance [m]' if type(self.fieldset.U.units) is UnitConverter else 'Longitude [degrees]'
            ylbl = 'Meridional distance [m]' if type(self.fieldset.U.units) is UnitConverter else 'Latitude [degrees]'
            plt.xlabel(xlbl)
            plt.ylabel(ylbl)
        elif Basemap is None:
            logger.info("Visualisation is not possible. Basemap not found.")
            time_origin = self.fieldset.U.grid.time_origin
        else:
            time_origin = self.fieldset.U.grid.time_origin
            (idx, periods) = self.fieldset.U.time_index(show_time)
            show_time -= periods*(self.fieldset.U.time[-1]-self.fieldset.U.time[0])
            U = np.array(self.fieldset.U.temporal_interpolate_fullfield(idx, show_time))
            V = np.array(self.fieldset.V.temporal_interpolate_fullfield(idx, show_time))
            lon = self.fieldset.U.lon
            lat = self.fieldset.U.lat
            lon = lon[lonW:lonE]
            lat = lat[latS:latN]
            U = U[latS:latN, lonW:lonE]
            V = V[latS:latN, lonW:lonE]

            # configuring plot
            lat_median = np.median(lat)
            lon_median = np.median(lon)
            plt.figure()
            m = Basemap(projection='merc', lat_0=lat_median, lon_0=lon_median,
                        resolution='h', area_thresh=100,
                        llcrnrlon=lon[0], llcrnrlat=lat[0],
                        urcrnrlon=lon[-1], urcrnrlat=lat[-1])
            if land:
                m.drawcoastlines()
                m.fillcontinents(color='burlywood')
            parallels = np.arange(lat[0], lat[-1], abs(lat[0]-lat[-1])/5)
            parallels = np.around(parallels, 2)
            m.drawparallels(parallels, labels=[1, 0, 0, 0])
            meridians = np.arange(lon[0], lon[-1], abs(lon[0]-lon[-1])/5)
            meridians = np.around(meridians, 2)
            m.drawmeridians(meridians, labels=[0, 0, 0, 1])

            # formating velocity data for quiver plotting
            U = np.array([U[y, x] for x in range(len(lon)) for y in range(len(lat))])
            V = np.array([V[y, x] for x in range(len(lon)) for y in range(len(lat))])
            speed = np.sqrt(U**2 + V**2)
            normU = U/speed
            normV = V/speed
            x = np.repeat(lon, len(lat))
            y = np.tile(lat, len(lon))

            # plotting velocity vector field
            vecs = m.quiver(x, y, normU, normV, speed, cmap=plt.cm.gist_ncar, clim=[vmin, vmax], scale=50, latlon=True)
            m.colorbar(vecs, "right", size="5%", pad="2%")
            # plotting particle data
            if particles:
                xs, ys = m(plon, plat)
                m.scatter(xs, ys, color='black')

        if time_origin is 0:
            timestr = ' after ' + str(delta(seconds=show_time)) + ' hours'
        else:
            timestr = ' on ' + str(time_origin + delta(seconds=show_time))

        if particles:
            if field:
                plt.title('Particles' + timestr)
            elif field is 'vector':
                plt.title('Particles and velocity field' + timestr)
            else:
                plt.title('Particles and '+namestr + timestr)
        else:
            if field is 'vector':
                plt.title('Velocity field' + timestr)
            else:
                plt.title(namestr + timestr)

        if savefile is None:
            plt.show()
            plt.pause(0.0001)
        else:
            plt.savefig(savefile)
            logger.info('Plot saved to '+savefile+'.png')
            plt.close()

    def density(self, field=None, particle_val=None, relative=False, area_scale=True):
        """Method to calculate the density of particles in a ParticleSet from their locations,
        through a 2D histogram

        :param field: Optional :mod:`parcels.field.Field` object to calculate the histogram
                    on. Default is `fieldset.U`
        :param particle_val: Optional list of values to weigh each particlewith
        :param relative: Boolean to control whether the density is scaled by the total
                    number of particles
        :param area_scale: Boolean to control whether the density is scaled by the area
                    (in m^2) of each grid cell"""
        lons = [p.lon for p in self.particles]
        lats = [p.lat for p in self.particles]
        # Code for finding nearest vertex for each particle is currently very inefficient
        # once cell tracking is implemented for SciPy particles, the below use of np.min/max
        # will be replaced (see PR #111)
        if field is not None:
            # Kick out particles that are not within the limits of our density field
            half_lon = (field.grid.lon[1] - field.grid.lon[0])/2
            half_lat = (field.grid.lat[1] - field.grid.lat[0])/2
            dparticles = (lons > (np.min(field.grid.lon)-half_lon)) * (lons < (np.max(field.grid.lon)+half_lon)) * \
                         (lats > (np.min(field.grid.lat)-half_lat)) * (lats < (np.max(field.grid.lat)+half_lat))
            dparticles = np.where(dparticles)[0]
        else:
            field = self.fieldset.U
            dparticles = range(len(self.particles))
        Density = np.zeros((field.grid.lon.size, field.grid.lat.size), dtype=np.float32)

        # For each particle, find closest vertex in x and y and add 1 or val to the count
        if particle_val is not None:
            for p in dparticles:
                Density[np.argmin(np.abs(lons[p] - field.grid.lon)), np.argmin(np.abs(lats[p] - field.grid.lat))] \
                    += getattr(self.particles[p], particle_val)
        else:
            for p in dparticles:
                nearest_lon = np.argmin(np.abs(lons[p] - field.grid.lon))
                nearest_lat = np.argmin(np.abs(lats[p] - field.grid.lat))
                Density[nearest_lon, nearest_lat] += 1
            if relative:
                Density /= len(dparticles)

        if area_scale:
            area = np.zeros(np.shape(field.data[0, :, :]), dtype=np.float32)
            U = self.fieldset.U
            V = self.fieldset.V
            dy = (V.grid.lon[1] - V.grid.lon[0])/V.units.to_target(1, V.grid.lon[0], V.grid.lat[0], V.grid.depth[0])
            for y in range(len(U.grid.lat)):
                dx = (U.grid.lon[1] - U.grid.lon[0])/U.units.to_target(1, U.grid.lon[0], U.grid.lat[y], V.grid.depth[0])
                area[y, :] = dy * dx
            # Scale by cell area
            Density /= np.transpose(area)

        return Density

    def Kernel(self, pyfunc):
        """Wrapper method to convert a `pyfunc` into a :class:`parcels.kernel.Kernel` object
        based on `fieldset` and `ptype` of the ParticleSet"""
        return Kernel(self.fieldset, self.ptype, pyfunc=pyfunc)

    def ParticleFile(self, *args, **kwargs):
        """Wrapper method to initialise a :class:`parcels.particlefile.ParticleFile`
        object from the ParticleSet"""
        return ParticleFile(*args, particleset=self, **kwargs)
