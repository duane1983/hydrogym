import firedrake as fd
from firedrake import dx, ds

import ufl
from ufl import dot, inner, nabla_grad, div, sym, curl

from typing import Optional

class FlowConfig:
    def __init__(self, mesh, h5_file=None):
        self.mesh = mesh
        self.n = fd.FacetNormal(self.mesh)
        self.x, self.y = fd.SpatialCoordinate(self.mesh)

        # Set up Taylor-Hood elements
        self.velocity_space = fd.VectorFunctionSpace(mesh, 'CG', 2)
        self.pressure_space = fd.FunctionSpace(mesh, 'CG', 1)
        self.mixed_space = fd.MixedFunctionSpace([self.velocity_space, self.pressure_space])
        self.q = fd.Function(self.mixed_space, name='q')
        self.split_solution()  # Break out and rename solution

        # TODO: Do this without having to reinitialize everything?
        if h5_file is not None:
            self.load_checkpoint(h5_file)

    def save_checkpoint(self, h5_file):
        with fd.CheckpointFile(h5_file, 'w') as chk:
            chk.save_mesh(self.mesh)  # optional
            chk.save_function(self.q)

    def load_checkpoint(self, h5_file):
        with fd.CheckpointFile(h5_file, 'r') as chk:
            mesh = chk.load_mesh('mesh')
            FlowConfig.__init__(self, mesh)  # Reinitialize with new mesh
            self.q = chk.load_function(self.mesh, 'q')
        
        self.split_solution()  # Reset functions so self.u, self.p point to the new solution

    def split_solution(self):
        self.u, self.p = self.q.split()
        self.u.rename('u')
        self.p.rename('p')

    def vorticity(self):
        vort = fd.project(curl(self.u), self.pressure_space)
        vort.rename('vort')
        return vort

    def init_bcs(self, mixed=False):
        """Define all boundary conditions"""
        pass

    def function_spaces(self, mixed=True):
        if mixed:
            V = self.mixed_space.sub(0)
            Q = self.mixed_space.sub(1)
        else:
            V = self.velocity_space
            Q = self.pressure_space
        return V, Q

    def collect_bcu(self):
        """List of velocity boundary conditions"""

    def collect_bcp(self):
        """List of pressure boundary conditions"""

    def collect_bcs(self):
        return self.collect_bcu() + self.collect_bcp()
    
    # Define symmetric gradient
    def epsilon(self, u):
        return sym(nabla_grad(u))

    # Define stress tensor
    def sigma(self, u, p):
        return 2*(1/self.Re)*self.epsilon(u) - p*fd.Identity(len(u))

    def steady_form(self, w, w_test):
        """Define nonlinear variational problem for steady-state NS"""
        pass

    def solve_steady(self):
        self.init_bcs(mixed=True)

        F = self.steady_form()  # Nonlinear variational form
        J = fd.derivative(F, self.q)    # Jacobian

        bcs = self.collect_bcs()
        problem = fd.NonlinearVariationalProblem(F, self.q, bcs, J)
        solver = fd.NonlinearVariationalSolver(problem)
        solver.solve()

        return self.q.copy(deepcopy=True)
    
    def steady_form(self, q=None):
        if q is None: q = self.q
        (u, p) = fd.split(q)
        (v, s) = fd.TestFunctions(self.mixed_space)
        nu = fd.Constant(1/ufl.real(self.Re))

        F  = inner(dot(u, nabla_grad(u)), v)*dx \
            + inner(self.sigma(u, p), self.epsilon(v))*dx \
            + inner(p*self.n, v)*ds - inner(nu*nabla_grad(u)*self.n, v)*ds \
            + inner(div(u), s)*dx
        return F

    def linearized_forms(self, qB):
        (u, _) = fd.TrialFunctions(self.mixed_space)
        (v, _) = fd.TestFunctions(self.mixed_space)
        F = self.steady_form(q=qB)
        L = -fd.derivative(F, qB)
        M = inner(u, v)*dx
        return L, M

    def linearize(self, qB, control=False, backend='petsc'):
        A_form, M_form = self.linearized_forms(qB)
        self.linearize_bcs()
        A = fd.assemble(A_form, bcs=self.collect_bcs()).petscmat  # Dynamics matrix
        M = fd.assemble(M_form, bcs=self.collect_bcs()).petscmat  # Mass matrix
        if control and self.num_controls()!=0:
            B = [self.linearize_control(i) for i in range(self.num_controls())]
            sys = M, A, B
        else:
            sys = M, A

        if backend=='scipy':
            from .utils import system_to_scipy
            sys = system_to_scipy(sys)
        return sys

    def linearize_control(self, act_idx=0):
        """Return a PETSc.Vec corresponding to the column of the control matrix"""
        pass

    def collect_observations(self):
        pass

    def set_control(self, u):
        pass

    def reset_control(self):
        pass

    def num_controls(self):
        return 0

class CallbackBase:
    def __init__(self, interval: Optional[int] = 1):
        self.interval = interval

    def __call__(self, iter: int, t: float, flow: FlowConfig):
        iostep = (iter % self.interval == 0)
        return iostep