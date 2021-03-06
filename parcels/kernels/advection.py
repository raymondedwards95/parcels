"""Collection of pre-built advection kernels"""
from parcels.kernels.error import ErrorCode
import math


__all__ = ['AdvectionRK4', 'AdvectionEE', 'AdvectionRK45', 'AdvectionRK4_3D']


def AdvectionRK4(particle, fieldset, time, dt):
    """Advection of particles using fourth-order Runge-Kutta integration.

    Function needs to be converted to Kernel object before execution"""
    (u1, v1) = fieldset.UV[time, particle.lon, particle.lat, particle.depth]
    lon1, lat1 = (particle.lon + u1*.5*dt, particle.lat + v1*.5*dt)
    (u2, v2) = fieldset.UV[time + .5 * dt, lon1, lat1, particle.depth]
    lon2, lat2 = (particle.lon + u2*.5*dt, particle.lat + v2*.5*dt)
    (u3, v3) = fieldset.UV[time + .5 * dt, lon2, lat2, particle.depth]
    lon3, lat3 = (particle.lon + u3*dt, particle.lat + v3*dt)
    (u4, v4) = fieldset.UV[time + dt, lon3, lat3, particle.depth]
    particle.lon += (u1 + 2*u2 + 2*u3 + u4) / 6. * dt
    particle.lat += (v1 + 2*v2 + 2*v3 + v4) / 6. * dt


def AdvectionRK4_3D(particle, fieldset, time, dt):
    """Advection of particles using fourth-order Runge-Kutta integration including vertical velocity.

    Function needs to be converted to Kernel object before execution"""
    (u1, v1) = fieldset.UV[time, particle.lon, particle.lat, particle.depth]
    w1 = fieldset.W[time, particle.lon, particle.lat, particle.depth]
    lon1 = particle.lon + u1*.5*dt
    lat1 = particle.lat + v1*.5*dt
    dep1 = particle.depth + w1*.5*dt
    (u2, v2) = fieldset.UV[time + .5 * dt, lon1, lat1, dep1]
    w2 = fieldset.W[time + .5 * dt, lon1, lat1, dep1]
    lon2 = particle.lon + u2*.5*dt
    lat2 = particle.lat + v2*.5*dt
    dep2 = particle.depth + w2*.5*dt
    (u3, v3) = fieldset.UV[time + .5 * dt, lon2, lat2, dep2]
    w3 = fieldset.W[time + .5 * dt, lon2, lat2, dep2]
    lon3 = particle.lon + u3*dt
    lat3 = particle.lat + v3*dt
    dep3 = particle.depth + w3*dt
    (u4, v4) = fieldset.UV[time + dt, lon3, lat3, dep3]
    w4 = fieldset.W[time + dt, lon3, lat3, dep3]
    particle.lon += (u1 + 2*u2 + 2*u3 + u4) / 6. * dt
    particle.lat += (v1 + 2*v2 + 2*v3 + v4) / 6. * dt
    particle.depth += (w1 + 2*w2 + 2*w3 + w4) / 6. * dt


def AdvectionEE(particle, fieldset, time, dt):
    """Advection of particles using Explicit Euler (aka Euler Forward) integration.

    Function needs to be converted to Kernel object before execution"""
    (u1, v1) = fieldset.UV[time, particle.lon, particle.lat, particle.depth]
    particle.lon += u1 * dt
    particle.lat += v1 * dt


def AdvectionRK45(particle, fieldset, time, dt):
    """Advection of particles using adadptive Runge-Kutta 4/5 integration.

    Times-step dt is halved if error is larger than tolerance, and doubled
    if error is smaller than 1/10th of tolerance, with tolerance set to
    1e-9 * dt by default."""
    tol = [1e-9]
    c = [1./4., 3./8., 12./13., 1., 1./2.]
    A = [[1./4., 0., 0., 0., 0.],
         [3./32., 9./32., 0., 0., 0.],
         [1932./2197., -7200./2197., 7296./2197., 0., 0.],
         [439./216., -8., 3680./513., -845./4104., 0.],
         [-8./27., 2., -3544./2565., 1859./4104., -11./40.]]
    b4 = [25./216., 0., 1408./2565., 2197./4104., -1./5.]
    b5 = [16./135., 0., 6656./12825., 28561./56430., -9./50., 2./55.]

    (u1, v1) = fieldset.UV[time, particle.lon, particle.lat, particle.depth]
    lon1, lat1 = (particle.lon + u1 * A[0][0] * dt,
                  particle.lat + v1 * A[0][0] * dt)
    (u2, v2) = fieldset.UV[time + c[0] * dt, lon1, lat1, particle.depth]
    lon2, lat2 = (particle.lon + (u1 * A[1][0] + u2 * A[1][1]) * dt,
                  particle.lat + (v1 * A[1][0] + v2 * A[1][1]) * dt)
    (u3, v3) = fieldset.UV[time + c[1] * dt, lon2, lat2, particle.depth]
    lon3, lat3 = (particle.lon + (u1 * A[2][0] + u2 * A[2][1] + u3 * A[2][2]) * dt,
                  particle.lat + (v1 * A[2][0] + v2 * A[2][1] + v3 * A[2][2]) * dt)
    (u4, v4) = fieldset.UV[time + c[2] * dt, lon3, lat3, particle.depth]
    lon4, lat4 = (particle.lon + (u1 * A[3][0] + u2 * A[3][1] + u3 * A[3][2] + u4 * A[3][3]) * dt,
                  particle.lat + (v1 * A[3][0] + v2 * A[3][1] + v3 * A[3][2] + v4 * A[3][3]) * dt)
    (u5, v5) = fieldset.UV[time + c[3] * dt, lon4, lat4, particle.depth]
    lon5, lat5 = (particle.lon + (u1 * A[4][0] + u2 * A[4][1] + u3 * A[4][2] + u4 * A[4][3] + u5 * A[4][4]) * dt,
                  particle.lat + (v1 * A[4][0] + v2 * A[4][1] + v3 * A[4][2] + v4 * A[4][3] + v5 * A[4][4]) * dt)
    (u6, v6) = fieldset.UV[time + c[4] * dt, lon5, lat5, particle.depth]

    lon_4th = particle.lon + (u1 * b4[0] + u2 * b4[1] + u3 * b4[2] + u4 * b4[3] + u5 * b4[4]) * dt
    lat_4th = particle.lat + (v1 * b4[0] + v2 * b4[1] + v3 * b4[2] + v4 * b4[3] + v5 * b4[4]) * dt
    lon_5th = particle.lon + (u1 * b5[0] + u2 * b5[1] + u3 * b5[2] + u4 * b5[3] + u5 * b5[4] + u6 * b5[5]) * dt
    lat_5th = particle.lat + (v1 * b5[0] + v2 * b5[1] + v3 * b5[2] + v4 * b5[3] + v5 * b5[4] + v6 * b5[5]) * dt

    kappa = math.sqrt(math.pow(lon_5th - lon_4th, 2) + math.pow(lat_5th - lat_4th, 2))
    if kappa <= math.fabs(dt * tol[0]):
        particle.lon = lon_4th
        particle.lat = lat_4th
        if kappa <= math.fabs(dt * tol[0] / 10):
            particle.dt *= 2
    else:
        particle.dt /= 2
        return ErrorCode.Repeat
