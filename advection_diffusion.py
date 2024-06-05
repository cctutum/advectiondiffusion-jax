import numpy as np
from scipy.sparse import csc_matrix
from scipy.sparse.linalg import splu
from scipy.optimize import minimize

import jax
import jax.numpy as jnp
from jax import jit
from jaxopt import ScipyBoundedMinimize

import matplotlib.pyplot as plt
import timeit

# Philip Mocz (2024)
# Solve the advection-diffusion equation using a finite difference method
# Plug it into an optimization problem to find the wind parameters that maximize pollution at the center of the domain
# Use either Nelder-Mead (SciPy) or autodiff (JAX) to solve the optimization problem

# Global variables
W = 0.5
diffusivity = 0.05
t_end = 0.25
N = 41 # XXX 81
M = 50
dx = 1.0 / (N-1)
dt = t_end / M
t = np.linspace(0, t_end, M+1)


# === Numpy version of the simulation ========================================

def index_function(i, j, N):
  # maps index (i,j) to the vector index in our solution vector 
  # (the grid size is N^2)
  return j*N + i


def initial_condition(x, y):
  # initial condition for the pollution
  return 2.0*np.exp(-100.0*((x-0.25)**2+(y-0.25)**2))+np.exp(-150.0*((x-0.65)**2+(y-0.4)**2))


def build_matrix(theta):
  # Construct the matrix (D) and its LU decomposition for the linear system to be solved at each time step
  D = np.eye(N**2, N**2)
  for i in range(1, N-1):
    for j in range(1, N-1):
      D[index_function(i,j,N),index_function(i,j,N)]   = dt*(1.0/dt + 4.0*diffusivity/dx**2)
      D[index_function(i,j,N),index_function(i+1,j,N)] = dt*( W*np.cos(theta)/(2.0*dx) - diffusivity/dx**2)
      D[index_function(i,j,N),index_function(i-1,j,N)] = dt*(-W*np.cos(theta)/(2.0*dx) - diffusivity/dx**2)
      D[index_function(i,j,N),index_function(i,j+1,N)] = dt*( W*np.sin(theta)/(2.0*dx) - diffusivity/dx**2)
      D[index_function(i,j,N),index_function(i,j-1,N)] = dt*(-W*np.sin(theta)/(2.0*dx) - diffusivity/dx**2)

  D = csc_matrix(D)  # sparse representation of the matrix
  B = splu(D)        # do an LU decomposition of the matrix

  return B


def do_simulation(x):
  # Solve the advection-diffusion equation using a finite difference method
  # Keep track of the pollution

  # Construct initial (t=0) solution
  xlin = np.linspace(0.0, 1.0, N)
  U = np.zeros(N**2)
  for i in range(1, N-1):
    for j in range(1, N-1):
      U[index_function(i,j,N)] = initial_condition(xlin[i], xlin[j])

  # Keep track of pollution as function of time
  pollution = np.zeros(M+1)
  ctr = index_function(N//2+1,N//2+1,N)
  pollution[0] = U[ctr]

  # Set the initial wind direction
  update_wind_direction = False
  i_wind = 0

  # Build the initial matrix
  B = build_matrix(x[i_wind])

  # Solve for the time evolution
  for i in range(M):

    # update the wind direction every 10 time steps
    update_wind_direction = (i>0 and i % 10 == 0)
    if(update_wind_direction):
      i_wind += 1
      B = build_matrix(x[i_wind])

    # solve the system
    U = B.solve(U)
    # record pollution at center of domain
    pollution[i+1] = U[ctr]
  
  pollution[M] = U[ctr]

  pollution_total = np.trapz(pollution, t)

  return U, pollution, pollution_total


def loss(x, info):
  # loss function that wraps the simulation
  _, _, pollution_total = do_simulation(x)

  # display information
  print('{0:4d}   {1: 3.6f}   {2: 3.6f}   {3: 3.6f}   {4: 3.6f}   {5: 3.6f} {6: 3.6f}'.format(info['Nfeval'], x[0], x[1], x[2], x[3], x[4], pollution_total))
  info['Nfeval'] += 1

  return -pollution_total


# === JAX version of the simulation ==========================================

@jit
def initial_condition_jax(x, y):
  # initial condition for the pollution -- JAX version
  return 2.0*jnp.exp(-100.0*((x-0.25)**2+(y-0.25)**2))+jnp.exp(-150.0*((x-0.65)**2+(y-0.4)**2))


@jit
def do_simulation_jax(x):
  # Solve the advection-diffusion equation with finite difference -- JAX version
  # Keep track of the pollution

  # Construct initial (t=0) solution
  xlin = jnp.linspace(0.0, 1.0, N)
  X, Y = jnp.meshgrid(xlin, xlin)
  U = initial_condition_jax(X, Y)
  U = U.at[0,:].set(0.0)
  U = U.at[-1,:].set(0.0)
  U = U.at[:,0].set(0.0)
  U = U.at[:,-1].set(0.0)
  U = U.flatten()

  # Keep track of pollution as function of time
  ctr = (N//2+1)*N + N//2+1
  pollution = jnp.zeros(M+1)
  pollution = pollution.at[0].set(U[ctr])

  # Define boundary indices
  bndry1 = jnp.arange(N)
  bndry2 = (N-1)*N + jnp.arange(N)
  bndry3 = jnp.arange(N)*N
  bndry4 = jnp.arange(N)*N + N-1
  bndry = jnp.concatenate((bndry1, bndry2, bndry3, bndry4))

  # Set the initial wind direction
  update_wind_direction = False
  i_wind = 0
  theta = x[i_wind]

  # Construct the matrix (D) and its LU decomposition for the linear system to be solved at each time step
  main_diag = jnp.ones(N**2)  * dt*(1.0/dt + 4.0*diffusivity/dx**2)
  off_diag1 = jnp.ones(N**2-1)* dt*( W*jnp.cos(theta)/(2.0*dx) - diffusivity/dx**2)
  off_diag2 = jnp.ones(N**2-1)* dt*(-W*jnp.cos(theta)/(2.0*dx) - diffusivity/dx**2)
  off_diag3 = jnp.ones(N**2-N)* dt*( W*jnp.sin(theta)/(2.0*dx) - diffusivity/dx**2)
  off_diag4 = jnp.ones(N**2-N)* dt*(-W*jnp.sin(theta)/(2.0*dx) - diffusivity/dx**2)
  
  D = jnp.diag(main_diag) + jnp.diag(off_diag1, 1) + jnp.diag(off_diag2, -1) + jnp.diag(off_diag3, N) + jnp.diag(off_diag4, -N)
  D = D.at[bndry, :].set(0.0)
  D = D.at[bndry, bndry].set(1.0)

  B = jax.scipy.linalg.lu_factor(D)  # do an LU decomposition of the matrix

  # Solve for the time evolution
  for i in range(M):
    # update the wind direction every 10 time steps
    update_wind_direction = (i>0 and i % 10 == 0)
    if(update_wind_direction):
      i_wind += 1
      theta = x[i_wind]
      off_diag1 = jnp.ones(N**2-1)* dt*( W*jnp.cos(theta)/(2.0*dx) - diffusivity/dx**2)
      off_diag2 = jnp.ones(N**2-1)* dt*(-W*jnp.cos(theta)/(2.0*dx) - diffusivity/dx**2)
      off_diag3 = jnp.ones(N**2-N)* dt*( W*jnp.sin(theta)/(2.0*dx) - diffusivity/dx**2)
      off_diag4 = jnp.ones(N**2-N)* dt*(-W*jnp.sin(theta)/(2.0*dx) - diffusivity/dx**2)

      D = jnp.diag(main_diag) + jnp.diag(off_diag1, 1) + jnp.diag(off_diag2, -1) + jnp.diag(off_diag3, N) + jnp.diag(off_diag4, -N)
      D = D.at[bndry, :].set(0.0)
      D = D.at[bndry, bndry].set(1.0)

      B = jax.scipy.linalg.lu_factor(D)  # do an LU decomposition of the matrix

    # solve the system
    U = jax.scipy.linalg.lu_solve(B, U)

    # record pollution at center of domain
    pollution = pollution.at[i+1].set(U[ctr])

  pollution = pollution.at[M].set(U[ctr])

  t = jnp.linspace(0, t_end, M+1)
  pollution_total = jnp.trapezoid(pollution, t)

  return U, pollution, pollution_total


@jit
def loss_jax(x):
  # loss function that wraps the simulation
  _, _, pollution_total = do_simulation_jax(x)

  return -pollution_total


# === Main ==================================================================

def main():

  # Wind parameters (initial guess)
  x0 = [np.pi/2.0] * 5
  
  # Optimize the wind parameters to find which values maximize the pollution
  bounds = [(0.0, np.pi)] * 5
  print("=== Numpy Approach =======================")
  start = timeit.default_timer()
  sol = minimize(loss, x0, args=({'Nfeval':0},), method='Nelder-Mead', tol=1e-8, bounds=bounds)
  print("Optimization process took:", timeit.default_timer() - start, "seconds")
  print('Optimized wind parameters:', sol.x)

  # Re-run the simulation with the optimized parameters and print the level of pollution
  start = timeit.default_timer()
  U, pollution, pollution_total = do_simulation(sol.x)
  print("Single Numpy run took:", timeit.default_timer() - start, "seconds")
  print('Total pollution:', pollution_total)

  # Carry out simulation with the optimized parameters
  print("=== JAX Approach =========================")
  start = timeit.default_timer()
  jbounds = [[0.0]*5, [np.pi]*5]
  optimizer = ScipyBoundedMinimize(fun=loss_jax, method='L-BFGS-B', tol = 1e-8)
  sol_jax = optimizer.run(init_params=x0, bounds=jbounds)
  print("Optimization process took:", timeit.default_timer() - start, "seconds")
  print(sol_jax)
  print('Optimized wind parameters:', sol_jax.params)

  # Re-run the simulation with the optimized parameters and print the level of pollution
  start = timeit.default_timer()
  U, pollution, pollution_total = do_simulation_jax(sol_jax.params)
  print("Single JAX run took:", timeit.default_timer() - start, "seconds")
  print('Total pollution:', pollution_total)

  # Plot the pollution as a function of time
  fig = plt.figure(figsize=(4,4), dpi=120)
  plt.plot(t, pollution, 'b-')
  plt.xlabel('Time')
  plt.ylabel('Pollution')
  plt.xlim(0, t_end)
  plt.ylim(0.0, 0.16)
  plt.show()

  # Plot the solution of the 2D pollution field
  fig = plt.figure(figsize=(4,4), dpi=120)
  U_plot = np.zeros((N, N))
  for i in range(N):
    for j in range(N):
      U_plot[j, i] = U[index_function(i, j, N)]

  plt.imshow(U_plot, cmap='Purples')
  plt.clim(0.0, 0.4)
  plt.contour(U_plot, levels=10, colors='black', alpha=0.5)
  plt.plot(0.5*N, 0.5*N, 'bs', markersize=8)
  ax = plt.gca()
  ax.invert_yaxis()
  ax.get_xaxis().set_visible(False)
  ax.get_yaxis().set_visible(False)	
  ax.set_aspect('equal')

  # Save figure
  plt.savefig('simulation.png',dpi=240)
  plt.show()

  return 0


if __name__== "__main__":
  main()
