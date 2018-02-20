from parcels import FieldSet, Field, ParticleSet, ScipyParticle, JITParticle, Variable, AdvectionRK4, AdvectionRK4_3D
from parcels import RectilinearZGrid, RectilinearSGrid, CurvilinearZGrid
from parcels import compute_curvilinearGrid_rotationAngles
import numpy as np
import math
import pytest
from os import path
from datetime import timedelta as delta

ptype = {'scipy': ScipyParticle, 'jit': JITParticle}


@pytest.mark.parametrize('mode', ['scipy', 'jit'])
def test_multi_structured_grids(mode):

    def temp_func(lon, lat):
        return 20 + lat/1000. + 2 * np.sin(lon*2*np.pi/5000.)

    a = 10000
    b = 10000

    # Grid 0
    xdim_g0 = 201
    ydim_g0 = 201
    # Coordinates of the test fieldset (on A-grid in deg)
    lon_g0 = np.linspace(0, a, xdim_g0, dtype=np.float32)
    lat_g0 = np.linspace(0, b, ydim_g0, dtype=np.float32)
    time_g0 = np.linspace(0., 1000., 2, dtype=np.float64)
    grid_0 = RectilinearZGrid(lon_g0, lat_g0, time=time_g0)

    # Grid 1
    xdim_g1 = 51
    ydim_g1 = 51
    # Coordinates of the test fieldset (on A-grid in deg)
    lon_g1 = np.linspace(0, a, xdim_g1, dtype=np.float32)
    lat_g1 = np.linspace(0, b, ydim_g1, dtype=np.float32)
    time_g1 = np.linspace(0., 1000., 2, dtype=np.float64)
    grid_1 = RectilinearZGrid(lon_g1, lat_g1, time=time_g1)

    u_data = np.ones((lon_g0.size, lat_g0.size, time_g0.size), dtype=np.float32)
    u_data = 2*u_data
    u_field = Field('U', u_data, grid=grid_0, transpose=True)

    temp0_data = np.empty((lon_g0.size, lat_g0.size, time_g0.size), dtype=np.float32)
    for i in range(lon_g0.size):
        for j in range(lat_g0.size):
            temp0_data[i, j, :] = temp_func(lon_g0[i], lat_g0[j])
    temp0_field = Field('temp0', temp0_data, grid=grid_0, transpose=True)

    v_data = np.zeros((lon_g1.size, lat_g1.size, time_g1.size), dtype=np.float32)
    v_field = Field('V', v_data, grid=grid_1, transpose=True)

    temp1_data = np.empty((lon_g1.size, lat_g1.size, time_g1.size), dtype=np.float32)
    for i in range(lon_g1.size):
        for j in range(lat_g1.size):
            temp1_data[i, j, :] = temp_func(lon_g1[i], lat_g1[j])
    temp1_field = Field('temp1', temp1_data, grid=grid_1, transpose=True)

    other_fields = {}
    other_fields['temp0'] = temp0_field
    other_fields['temp1'] = temp1_field

    field_set = FieldSet(u_field, v_field, fields=other_fields)

    def sampleTemp(particle, fieldset, time, dt):
        # Note that fieldset.temp is interpolated at time=time+dt.
        # Indeed, sampleTemp is called at time=time, but the result is written
        # at time=time+dt, after the Kernel update
        particle.temp0 = fieldset.temp0[time+dt, particle.lon, particle.lat, particle.depth]
        particle.temp1 = fieldset.temp1[time+dt, particle.lon, particle.lat, particle.depth]

    class MyParticle(ptype[mode]):
        temp0 = Variable('temp0', dtype=np.float32, initial=20.)
        temp1 = Variable('temp1', dtype=np.float32, initial=20.)

    pset = ParticleSet.from_list(field_set, MyParticle, lon=[3001], lat=[5001])

    pset.execute(AdvectionRK4 + pset.Kernel(sampleTemp), runtime=1, dt=1)

    assert np.allclose(pset.particles[0].temp0, pset.particles[0].temp1, atol=1e-3)


def test_avoid_repeated_grids():

    lon_g0 = np.linspace(0, 1000, 11, dtype=np.float32)
    lat_g0 = np.linspace(0, 1000, 11, dtype=np.float32)
    time_g0 = np.linspace(0, 1000, 2, dtype=np.float64)
    grid_0 = RectilinearZGrid(lon_g0, lat_g0, time=time_g0)

    lon_g1 = np.linspace(0, 1000, 21, dtype=np.float32)
    lat_g1 = np.linspace(0, 1000, 21, dtype=np.float32)
    time_g1 = np.linspace(0, 1000, 2, dtype=np.float64)
    grid_1 = RectilinearZGrid(lon_g1, lat_g1, time=time_g1)

    u_data = np.zeros((lon_g0.size, lat_g0.size, time_g0.size), dtype=np.float32)
    u_field = Field('U', u_data, grid=grid_0, transpose=True)

    v_data = np.zeros((lon_g1.size, lat_g1.size, time_g1.size), dtype=np.float32)
    v_field = Field('V', v_data, grid=grid_1, transpose=True)

    temp0_field = Field('temp', u_data, lon=lon_g0, lat=lat_g0, time=time_g0, transpose=True)

    other_fields = {}
    other_fields['temp0'] = temp0_field

    field_set = FieldSet(u_field, v_field, fields=other_fields)
    assert field_set.gridset.size == 2
    assert field_set.U.grid is field_set.temp.grid
    assert field_set.V.grid is not field_set.U.grid


@pytest.mark.parametrize('mode', ['scipy', 'jit'])
def test_multigrids_pointer(mode):
    lon_g0 = np.linspace(0, 1e4, 21, dtype=np.float32)
    lat_g0 = np.linspace(0, 1000, 2, dtype=np.float32)
    depth_g0 = np.zeros((lon_g0.size, lat_g0.size, 5), dtype=np.float32)

    def bath_func(lon):
        return lon / 1000. + 10
    bath = bath_func(lon_g0)

    for i in range(depth_g0.shape[0]):
        for k in range(depth_g0.shape[2]):
            depth_g0[i, :, k] = bath[i] * k / (depth_g0.shape[2]-1)

    grid_0 = RectilinearSGrid(lon_g0, lat_g0, depth=depth_g0)
    grid_1 = RectilinearSGrid(lon_g0, lat_g0, depth=depth_g0)

    u_data = np.zeros((lon_g0.size, lat_g0.size, depth_g0.shape[2]), dtype=np.float32)
    v_data = np.zeros((lon_g0.size, lat_g0.size, depth_g0.shape[2]), dtype=np.float32)
    w_data = np.zeros((lon_g0.size, lat_g0.size, depth_g0.shape[2]), dtype=np.float32)

    u_field = Field('U', u_data, grid=grid_0, transpose=True)
    v_field = Field('V', v_data, grid=grid_0, transpose=True)
    w_field = Field('W', w_data, grid=grid_1, transpose=True)

    field_set = FieldSet(u_field, v_field, fields={'W': w_field})
    assert(u_field.grid == v_field.grid)
    assert(u_field.grid == w_field.grid)  # w_field.grid is now supposed to be grid_1

    pset = ParticleSet.from_list(field_set, ptype[mode], lon=[0], lat=[0], depth=[1])

    for i in range(10):
        pset.execute(AdvectionRK4_3D, runtime=1000, dt=500)


@pytest.mark.parametrize('mode', ['scipy', 'jit'])
@pytest.mark.parametrize('z4d', ['True', 'False'])
def test_rectilinear_s_grid_sampling(mode, z4d):
    lon_g0 = np.linspace(-3e4, 3e4, 61, dtype=np.float32)
    lat_g0 = np.linspace(0, 1000, 2, dtype=np.float32)
    time_g0 = np.linspace(0, 1000, 2, dtype=np.float64)
    if z4d:
        depth_g0 = np.zeros((lon_g0.size, lat_g0.size, 5, time_g0.size), dtype=np.float32)
    else:
        depth_g0 = np.zeros((lon_g0.size, lat_g0.size, 5), dtype=np.float32)

    def bath_func(lon):
        bath = (lon <= -2e4) * 20.
        bath += (lon > -2e4) * (lon < 2e4) * (110. + 90 * np.sin(lon/2e4 * np.pi/2.))
        bath += (lon >= 2e4) * 200.
        return bath
    bath = bath_func(lon_g0)

    for i in range(depth_g0.shape[0]):
        for k in range(depth_g0.shape[2]):
            if z4d:
                depth_g0[i, :, k, :] = bath[i] * k / (depth_g0.shape[2]-1)
            else:
                depth_g0[i, :, k] = bath[i] * k / (depth_g0.shape[2]-1)

    grid = RectilinearSGrid(lon_g0, lat_g0, depth=depth_g0, time=time_g0)

    u_data = np.zeros((lon_g0.size, lat_g0.size, depth_g0.shape[2], time_g0.size), dtype=np.float32)
    v_data = np.zeros((lon_g0.size, lat_g0.size, depth_g0.shape[2], time_g0.size), dtype=np.float32)
    temp_data = np.zeros((lon_g0.size, lat_g0.size, depth_g0.shape[2], time_g0.size), dtype=np.float32)
    for k in range(1, depth_g0.shape[2]):
        temp_data[:, :, k, :] = k / (depth_g0.shape[2]-1.)
    u_field = Field('U', u_data, grid=grid, transpose=True)
    v_field = Field('V', v_data, grid=grid, transpose=True)
    temp_field = Field('temp', temp_data, grid=grid, transpose=True)

    other_fields = {}
    other_fields['temp'] = temp_field
    field_set = FieldSet(u_field, v_field, fields=other_fields)

    def sampleTemp(particle, fieldset, time, dt):
        particle.temp = fieldset.temp[time, particle.lon, particle.lat, particle.depth]

    class MyParticle(ptype[mode]):
        temp = Variable('temp', dtype=np.float32, initial=20.)

    lon = 400
    lat = 0
    ratio = .3
    pset = ParticleSet.from_list(field_set, MyParticle, lon=[lon], lat=[lat], depth=[bath_func(lon)*ratio])

    pset.execute(pset.Kernel(sampleTemp), runtime=0, dt=0)
    assert np.allclose(pset.particles[0].temp, ratio, atol=1e-4)


@pytest.mark.parametrize('mode', ['scipy', 'jit'])
def test_rectilinear_s_grids_advect1(mode):
    # Constant water transport towards the east. check that the particle stays at the same relative depth (z/bath)
    lon_g0 = np.linspace(0, 1e4, 21, dtype=np.float32)
    lat_g0 = np.linspace(0, 1000, 2, dtype=np.float32)
    depth_g0 = np.zeros((lon_g0.size, lat_g0.size, 5), dtype=np.float32)

    def bath_func(lon):
        return lon / 1000. + 10
    bath = bath_func(lon_g0)

    for i in range(depth_g0.shape[0]):
        for k in range(depth_g0.shape[2]):
            depth_g0[i, :, k] = bath[i] * k / (depth_g0.shape[2]-1)

    grid = RectilinearSGrid(lon_g0, lat_g0, depth=depth_g0)

    u_data = np.zeros((lon_g0.size, lat_g0.size, depth_g0.shape[2]), dtype=np.float32)
    v_data = np.zeros((lon_g0.size, lat_g0.size, depth_g0.shape[2]), dtype=np.float32)
    w_data = np.zeros((lon_g0.size, lat_g0.size, depth_g0.shape[2]), dtype=np.float32)
    for i in range(depth_g0.shape[0]):
        u_data[i, :, :] = 1 * 10 / bath[i]
        for k in range(depth_g0.shape[2]):
            w_data[i, :, k] = u_data[i, :, k] * depth_g0[i, :, k] / bath[i] * 1e-3

    u_field = Field('U', u_data, grid=grid, transpose=True)
    v_field = Field('V', v_data, grid=grid, transpose=True)
    w_field = Field('W', w_data, grid=grid, transpose=True)

    field_set = FieldSet(u_field, v_field, fields={'W': w_field})

    lon = np.zeros((11))
    lat = np.zeros((11))
    ratio = [min(i/10., .99) for i in range(11)]
    depth = bath_func(lon)*ratio
    pset = ParticleSet.from_list(field_set, ptype[mode], lon=lon, lat=lat, depth=depth)

    pset.execute(AdvectionRK4_3D, runtime=10000, dt=500)
    assert np.allclose([p.depth/bath_func(p.lon) for p in pset], ratio)


@pytest.mark.parametrize('mode', ['scipy', 'jit'])
def test_rectilinear_s_grids_advect2(mode):
    # Move particle towards the east, check relative depth evolution
    lon_g0 = np.linspace(0, 1e4, 21, dtype=np.float32)
    lat_g0 = np.linspace(0, 1000, 2, dtype=np.float32)
    depth_g0 = np.zeros((lon_g0.size, lat_g0.size, 5), dtype=np.float32)

    def bath_func(lon):
        return lon / 1000. + 10
    bath = bath_func(lon_g0)

    for i in range(depth_g0.shape[0]):
        for k in range(depth_g0.shape[2]):
            depth_g0[i, :, k] = bath[i] * k / (depth_g0.shape[2]-1)

    grid = RectilinearSGrid(lon_g0, lat_g0, depth=depth_g0)

    u_data = np.zeros((lon_g0.size, lat_g0.size, depth_g0.shape[2]), dtype=np.float32)
    v_data = np.zeros((lon_g0.size, lat_g0.size, depth_g0.shape[2]), dtype=np.float32)
    rel_depth_data = np.zeros((lon_g0.size, lat_g0.size, depth_g0.shape[2]), dtype=np.float32)
    for k in range(1, depth_g0.shape[2]):
        rel_depth_data[:, :, k] = k / (depth_g0.shape[2]-1.)

    u_field = Field('U', u_data, grid=grid, transpose=True)
    v_field = Field('V', v_data, grid=grid, transpose=True)
    rel_depth_field = Field('relDepth', rel_depth_data, grid=grid, transpose=True)
    field_set = FieldSet(u_field, v_field, fields={'relDepth': rel_depth_field})

    class MyParticle(ptype[mode]):
        relDepth = Variable('relDepth', dtype=np.float32, initial=20.)

    def moveEast(particle, fieldset, time, dt):
        particle.lon += 5 * dt
        particle.relDepth = fieldset.relDepth[time, particle.lon, particle.lat, particle.depth]

    depth = .9
    pset = ParticleSet.from_list(field_set, MyParticle, lon=[0], lat=[0], depth=[depth])

    kernel = pset.Kernel(moveEast)
    for _ in range(10):
        pset.execute(kernel, runtime=100, dt=50)
        assert np.allclose(pset[0].relDepth, depth/bath_func(pset[0].lon))


@pytest.mark.parametrize('mode', ['scipy', 'jit'])
def test_curvilinear_grids(mode):

    x = np.linspace(0, 1e3, 7, dtype=np.float32)
    y = np.linspace(0, 1e3, 5, dtype=np.float32)
    (xx, yy) = np.meshgrid(x, y)

    r = np.sqrt(xx*xx+yy*yy)
    theta = np.arctan2(yy, xx)
    theta = theta + np.pi/6.

    lon = r * np.cos(theta)
    lat = r * np.sin(theta)
    time = np.array([0, 86400], dtype=np.float64)
    grid = CurvilinearZGrid(lon, lat, time=time)

    u_data = np.ones((2, y.size, x.size), dtype=np.float32)
    v_data = np.zeros((2, y.size, x.size), dtype=np.float32)
    u_data[0, :, :] = lon[:, :] + lat[:, :]
    u_field = Field('U', u_data, grid=grid, transpose=False)
    v_field = Field('V', v_data, grid=grid, transpose=False)
    field_set = FieldSet(u_field, v_field)

    def sampleSpeed(particle, fieldset, time, dt):
        u = fieldset.U[time, particle.lon, particle.lat, particle.depth]
        v = fieldset.V[time, particle.lon, particle.lat, particle.depth]
        particle.speed = math.sqrt(u*u+v*v)

    class MyParticle(ptype[mode]):
        speed = Variable('speed', dtype=np.float32, initial=0.)

    pset = ParticleSet.from_list(field_set, MyParticle, lon=[400], lat=[600])
    pset.execute(pset.Kernel(sampleSpeed), runtime=0, dt=0)
    assert(np.allclose(pset[0].speed, 1000))


@pytest.mark.parametrize('mode', ['scipy', 'jit'])
def test_nemo_grid(mode):
    data_path = path.join(path.dirname(__file__), 'test_data/')

    mesh_filename = data_path + 'mask_nemo_cross_180lon.nc'
    rotation_angles_filename = data_path + 'rotation_angles_nemo_cross_180lon.nc'
    compute_curvilinearGrid_rotationAngles(mesh_filename, rotation_angles_filename)

    filenames = {'U': data_path + 'Uu_eastward_nemo_cross_180lon.nc',
                 'V': data_path + 'Vv_eastward_nemo_cross_180lon.nc',
                 'cosU': rotation_angles_filename,
                 'sinU': rotation_angles_filename,
                 'cosV': rotation_angles_filename,
                 'sinV': rotation_angles_filename}
    variables = {'U': 'U',
                 'V': 'V',
                 'cosU': 'cosU',
                 'sinU': 'sinU',
                 'cosV': 'cosV',
                 'sinV': 'sinV'}

    dimensions = {'U': {'lon': 'nav_lon_u', 'lat': 'nav_lat_u'},
                  'V': {'lon': 'nav_lon_v', 'lat': 'nav_lat_v'},
                  'cosU': {'lon': 'glamu', 'lat': 'gphiu'},
                  'sinU': {'lon': 'glamu', 'lat': 'gphiu'},
                  'cosV': {'lon': 'glamv', 'lat': 'gphiv'},
                  'sinV': {'lon': 'glamv', 'lat': 'gphiv'}}

    field_set = FieldSet.from_netcdf(filenames, variables, dimensions, mesh='spherical')

    def sampleVel(particle, fieldset, time, dt):
        (particle.zonal, particle.meridional) = fieldset.UV[time, particle.lon, particle.lat, particle.depth]

    class MyParticle(ptype[mode]):
        zonal = Variable('zonal', dtype=np.float32, initial=0.)
        meridional = Variable('meridional', dtype=np.float32, initial=0.)

    lonp = 175.5
    latp = 81.5
    pset = ParticleSet.from_list(field_set, MyParticle, lon=[lonp], lat=[latp])
    pset.execute(pset.Kernel(sampleVel), runtime=0, dt=0)
    u = field_set.U.units.to_source(pset[0].zonal, lonp, latp, 0)
    v = field_set.V.units.to_source(pset[0].meridional, lonp, latp, 0)
    assert abs(u - 1) < 1e-4
    assert abs(v) < 1e-4


@pytest.mark.parametrize('mode', ['scipy', 'jit'])
def test_advect_nemo(mode):
    data_path = path.join(path.dirname(__file__), 'test_data/')

    mesh_filename = data_path + 'mask_nemo_cross_180lon.nc'
    rotation_angles_filename = data_path + 'rotation_angles_nemo_cross_180lon.nc'
    variables = {'cosU': 'cosU',
                 'sinU': 'sinU',
                 'cosV': 'cosV',
                 'sinV': 'sinV'}
    dimensions = {'U': {'lon': 'glamu', 'lat': 'gphiu'},
                  'V': {'lon': 'glamv', 'lat': 'gphiv'},
                  'F': {'lon': 'glamf', 'lat': 'gphif'}}
    compute_curvilinearGrid_rotationAngles(mesh_filename, rotation_angles_filename, variables, dimensions)

    filenames = {'U': data_path + 'Uu_eastward_nemo_cross_180lon.nc',
                 'V': data_path + 'Vv_eastward_nemo_cross_180lon.nc',
                 'cosU': rotation_angles_filename,
                 'sinU': rotation_angles_filename,
                 'cosV': rotation_angles_filename,
                 'sinV': rotation_angles_filename}
    variables = {'U': 'U',
                 'V': 'V',
                 'cosU': 'cosU',
                 'sinU': 'sinU',
                 'cosV': 'cosV',
                 'sinV': 'sinV'}

    dimensions = {'U': {'lon': 'nav_lon_u', 'lat': 'nav_lat_u'},
                  'V': {'lon': 'nav_lon_v', 'lat': 'nav_lat_v'},
                  'cosU': {'lon': 'glamu', 'lat': 'gphiu'},
                  'sinU': {'lon': 'glamu', 'lat': 'gphiu'},
                  'cosV': {'lon': 'glamv', 'lat': 'gphiv'},
                  'sinV': {'lon': 'glamv', 'lat': 'gphiv'}}

    field_set = FieldSet.from_netcdf(filenames, variables, dimensions, mesh='spherical', allow_time_extrapolation=True)

    lonp = 175.5
    latp = 81.5
    pset = ParticleSet.from_list(field_set, ptype[mode], lon=[lonp], lat=[latp])
    pset.execute(AdvectionRK4, runtime=delta(days=2), dt=delta(hours=6))
    assert abs(pset[0].lat - latp) < 1e-3
