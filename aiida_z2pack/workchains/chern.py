import numpy as np

from aiida import orm
from aiida.common import AttributeDict
from aiida.plugins import WorkflowFactory, DataFactory
from aiida.engine import WorkChain, ToContext, if_, while_, append_

from aiida_quantumespresso.utils.mapping import prepare_process_inputs

from .functions import generate_cubic_grid, get_kpoint_grid_dimensionality, get_crossing_and_lowgap_points, merge_crossing_results

# Z2packCalculation   = CalculationFactory('z2pack.z2pack')

PwBaseWorkChain     = WorkflowFactory('quantumespresso.pw.base')
PwRelaxWorkChain    = WorkflowFactory('quantumespresso.pw.relax')
Z2packBaseWorkChain = WorkflowFactory('z2pack.base')

ArrayData = DataFactory('array')

class FindCrossingsWorkChain(WorkChain):
    """Workchain to find bands crossing in the Brillouin Zone using
    a series of quantum espresso nscf calculations."""

    @classmethod
    def define(cls, spec):
        super().define(spec)

        # INPUTS ############################################################################
        spec.input(
            'code', valid_type=orm.Code,
            help='The PWscf code.'
            )
        spec.input(
            'structure', valid_type=orm.StructureData,
            help='The inputs structure.'
            )
        spec.input_namespace(
            'pseudos', valid_type=orm.UpfData,
            dynamic=True,
            help='A mapping of `UpfData` nodes onto the kind name to which they should apply.'
            )
        spec.input(
            'clean_workdir', valid_type=orm.Bool,
            default=orm.Bool(False),
            help='If `True`, work directories of all called calculation will be cleaned at the end of execution.'
            )
        spec.expose_inputs(
            PwRelaxWorkChain, namespace='relax',
            exclude=('clean_workdir', 'structure', 'base.pw.code', 'base.pw.pseudos'),
            namespace_options={
                'required': False, 'populate_defaults': False,
                'help': 'Inputs for the `PwBaseWorkChain` for the RELAX calculation.'
                }
            )
        spec.expose_inputs(
            PwBaseWorkChain, namespace='scf',
            exclude=('clean_workdir', 'pw.structure', 'pw.code', 'pw.pseudos'),
            namespace_options={
                'help': 'Inputs for the `PwBaseWorkChain` for the SCF calculation.'
                }
            )
        spec.expose_inputs(
            PwBaseWorkChain, namespace='nscf',
            exclude=('clean_workdir', 'pw.structure', 'pw.code', 'pw.pseudos', 'kpoints'),
            namespace_options={
                'required': False, 'populate_defaults': False,
                'help': 'Inputs for the `PwBaseWorkChain` for the NSCF calculation.'
                }
            )

        spec.input(
            'min_kpoints_distance', valid_type=orm.Float,
            default=orm.Float(1E-4),
            help='Stop iterations when `kpoints_distance`  drop below this value.'
            )
        spec.input(
            'starting_kpoints_distance', valid_type=orm.Float,
            default=orm.Float(0.05),
            help='Strating distance between kpoints.'
            )
        spec.input(
            'scale_kpoints_distance', valid_type=orm.Float,
            default=orm.Float(0.05),
            help='Across iterations divide `kpoints_distance` by this scaling factor.'
            )
        spec.input(
            'starting_kpoints', valid_type=orm.KpointsData,
            required=True,
            help='Starting mesh of kpoints'
            )
        spec.input(
            'starting_gap_threshold', valid_type=orm.Float,
            default=orm.Float(0.3),
            help=(
                'Starting value for `gap_threshold`. All kpoints with a gap between valence and conduction '
                'lower than this threshold will be selected for the successive loops.'
                )
            )
        spec.input(
            'min_gap_threshold', valid_type=orm.Float,
            default=orm.Float(0.001),
            help=('Across iterations, `gap_threshold` will never drop below this value.'
                )
            )
        spec.input(
            'scale_gap_threshold', valid_type=orm.Float,
            default=orm.Float(5.0),
            help=('Between every iteration, divide `gap_threshold` by this scaling factor.'
                )
            )

        # OUTLINE ############################################################################
        spec.outline(
            cls.setup,
            if_(cls.should_do_relax)(
                cls.run_relax,
                cls.inspect_relax,
            ),
            cls.run_scf,
            cls.inspect_scf,
            cls.setup_nscf_loop,
            while_(cls.should_find_zero_gap)(
                cls.setup_grid,
                cls.run_nscf,
                cls.analyze_bands,
                # cls.post_analysis,
                cls.stepper
                ),
            cls.results
            )

        # OUTPUTS ############################################################################
        spec.output('crossings', valid_type=ArrayData,
            required=True,
            help='The array containing a list of bands crossing found as rows.'
            )

        # ERRORS ############################################################################
        spec.exit_code(112, 'ERROR_SUB_PROCESS_FAILED_RELAX',
            message='the relax PwRelaxWorkChain sub process failed')
        spec.exit_code(122, 'ERROR_SUB_PROCESS_FAILED_SCF',
            message='the scf PwBaseWorkChain sub process failed')
        spec.exit_code(132, 'ERROR_SUB_PROCESS_FAILED_NSCF',
            message='the nscf PwBaseWorkChain sub process failed')
        spec.exit_code(142, 'ERROR_CANT_PINPOINT_LOWGAP_ZONE',
            message='After two iterations, no points with low_gap found. Aborting calculation!')
        spec.exit_code(152, 'ERROR_MAXIMUM_ITERATIONS_EXCEEDED',
            message='The maximum number of iterations was exceeded.')
        spec.exit_code(162, 'ERROR_TOO_MANY_ARRAYS',
            message='An ArrayData node contains more arrays than expected.')
        spec.exit_code(172, 'ERROR_MEMORY_TOO_MANY_KPOINTS',
            message='The generation of the kpoints failed because the mesh size was too big.')

    def setup(self):
        """Define the current structure in the context to be the input structure."""
        self.ctx.pseudos           = self.inputs.pseudos
        self.ctx.current_structure = self.inputs.structure
        
    def should_do_relax(self):
        """If the 'relax' input namespace was specified, we relax the input structure."""
        return 'relax' in self.inputs

    def run_relax(self):
        """Run the PwRelaxWorkChain to run a relax PwCalculation."""
        inputs = AttributeDict(self.exposed_inputs(PwRelaxWorkChain, namespace='relax'))
        inputs.structure = self.ctx.current_structure
        inputs.base.pw.pseudos = self.inputs.pseudos
        inputs.base.pw.code    = self.inputs.code
        inputs.clean_workdir   = self.inputs.clean_workdir

        running = self.submit(PwRelaxWorkChain, **inputs)

        self.report('launching PwRelaxWorkChain<{}>'.format(running.pk))

        return ToContext(workchain_relax=running)

    def inspect_relax(self):
        """Verify that the PwRelaxWorkChain finished successfully."""
        workchain = self.ctx.workchain_relax

        if not workchain.is_finished_ok:
            self.report('PwRelaxWorkChain failed with exit status {}'.format(workchain.exit_status))
            return self.exit_codes.ERROR_SUB_PROCESS_FAILED_RELAX

        self.ctx.current_structure = workchain.outputs.output_structure

    def run_scf(self):
        """Run the PwBaseWorkChain in scf mode on the primitive cell of (optionally relaxed) input structure."""
        inputs = AttributeDict(self.exposed_inputs(PwBaseWorkChain, namespace='scf'))
        inputs.clean_workdir = self.inputs.clean_workdir
        inputs.pw.pseudos    = self.inputs.pseudos
        inputs.pw.code       = self.inputs.code
        inputs.pw.structure  = self.ctx.current_structure
        inputs.pw.parameters = inputs.pw.parameters.get_dict()
        inputs.pw.parameters.setdefault('CONTROL', {})
        inputs.pw.parameters['CONTROL']['calculation'] = 'scf'

        inputs = prepare_process_inputs(PwBaseWorkChain, inputs)
        running = self.submit(PwBaseWorkChain, **inputs)

        self.report('launching PwBaseWorkChain<{}> in {} mode'.format(running.pk, 'scf'))

        return ToContext(workchain_scf=running)

    def inspect_scf(self):
        """Verify that the PwBaseWorkChain for the scf run finished successfully."""
        workchain = self.ctx.workchain_scf

        if not workchain.is_finished_ok:
            self.report('scf PwBaseWorkChain failed with exit status {}'.format(workchain.exit_status))
            return self.exit_codes.ERROR_SUB_PROCESS_FAILED_SCF

        self.ctx.scf_folder = workchain.outputs.remote_folder

    def setup_nscf_loop(self):
        self.ctx.iteration = 0
        # self.ctx.max_iteration = self.inputs.max_iter
        if 'nscf' in self.inputs:
            self.ctx.inputs = AttributeDict(self.exposed_inputs(PwBaseWorkChain, namespace='nscf'))
        else:
            self.ctx.inputs = AttributeDict(self.exposed_inputs(PwBaseWorkChain, namespace='scf'))

        self.ctx.inputs.pw.parameters = self.ctx.inputs.pw.parameters.get_dict()
        self.ctx.inputs.pw.parameters.setdefault('CONTROL', {})
        self.ctx.inputs.pw.parameters['CONTROL']['calculation'] = 'nscf'
        self.ctx.inputs.pw.parent_folder = self.ctx.scf_folder
        self.ctx.inputs.clean_workdir = self.inputs.clean_workdir
        self.ctx.inputs.pw.structure  = self.ctx.current_structure
        self.ctx.inputs.pw.pseudos    = self.inputs.pseudos
        self.ctx.inputs.pw.code       = self.inputs.code

        workchain = self.ctx.workchain_scf
        pw_params = workchain.outputs.output_parameters.get_dict()
        n_el      = pw_params['number_of_electrons']
        spin      = pw_params['spin_orbit_calculation']

        self.ctx.n_el = n_el
        self.ctx.cb   = int(n_el) // (int(not spin) + 1)
        self.ctx.vb   = self.ctx.cb - 1

        self.ctx.current_kpoints_distance  = self.inputs.starting_kpoints_distance.value
        self.ctx.min_kpoints_distance      = self.inputs.min_kpoints_distance.value
        self.ctx.scale_kpoints_distance    = self.inputs.scale_kpoints_distance.value

        self.ctx.current_gap_threshold  = self.inputs.starting_gap_threshold.value
        self.ctx.min_gap_threshold      = self.inputs.min_gap_threshold.value
        self.ctx.scale_gap_threshold    = self.inputs.scale_gap_threshold.value

        self.ctx.dim = get_kpoint_grid_dimensionality(self.inputs.starting_kpoints)

        self.ctx.found_crossings = []
        self.ctx.do_loop = True

        self.report('Starting loop to find bands crossings.')

    def should_find_zero_gap(self):
        """Limit iterations over kpoints meshes."""
        return  self.ctx.do_loop and self.ctx.current_kpoints_distance >= self.ctx.min_kpoints_distance

    def setup_grid(self):
        # mesh = self.ctx.current_mesh
        if self.ctx.iteration == 0:
            self.ctx.current_kpoints = self.inputs.starting_kpoints
        else:
            distance = orm.Float(self.ctx.current_kpoints_distance)
            self.ctx.current_kpoints = generate_cubic_grid(
                self.ctx.structure, self.ctx.found_crossings[-1], distance, self.ctx.dim
                )

    def run_nscf(self):
        self.ctx.iteration += 1
        inputs = self.ctx.inputs
        inputs.kpoints = self.ctx.current_kpoints

        inputs = prepare_process_inputs(PwBaseWorkChain, inputs)
        running = self.submit(PwBaseWorkChain, **inputs)

        self.report('launching PwBaseWorkChain<{}> in {} mode, iteration {}'.format(
            running.pk, 'nscf', self.ctx.iteration
            ))

        return ToContext(workchain_nscf=append_(running))
    
    def inspect_nscf(self):
        """Verify that the PwBaseWorkChain for the nscf run finished successfully."""
        workchain = self.ctx.workchain_nscf[self.ctx.iteration - 1]

        if not workchain.is_finished_ok:
            self.report('scf PwBaseWorkChain failed with exit status {}'.format(workchain.exit_status))
            return self.exit_codes.ERROR_SUB_PROCESS_FAILED_NSCF

        self.ctx.bands = workchain.outputs.output_band

    def analyze_bands(self):
        """Extract kpoints with gap lower than the gap threshold"""
        self.report('Analyzing nscf results for BandsData')
        workchain = self.ctx.workchain_nscf[self.ctx.iteration - 1]
        bands      = workchain.outputs.output_band

        vb        = self.ctx.vb
        cb        = self.ctx.cb

        vb_cb = orm.ArrayData()
        vb_cb.set_array('vb_cb', np.array([vb, cb]))

        res = get_crossing_and_lowgap_points(
            bands, vb_cb,
            orm.Float(self.ctx.current_gap_threshold), orm.Float(self.ctx.min_gap_threshold)
            )

        self.ctx.found_crossings.append(res)

    def stepper(self):
        """Perform the loop step operation of modifying the thresholds"""

        self.ctx.current_kpoints_distance /= self.ctx.scale_kpoints_distance

        cgt = self.ctx.current_gap_threshold
        mgt = self.ctx.min_gap_threshold
        sgt = self.ctx.scale_gap_threshold

        self.ctx.current_gap_threshold = max(cgt/sgt, mgt)

        last = self.ctx.found_crossings[-1]
        pinned = last.get_array('pinned')
        found = last.get_array('found')

        n_saved = len(pinned) + len(found)
        if n_saved:
            self.report('`{}` points found with gap lower than the threshold `{}`'.format(n_saved, cgt))

            self.report('Gap threshold reduced to `{}`'.format(self.ctx.current_gap_threshold))
            self.report('Kpoints distance reduced to `{}`'.format(self.ctx.current_kpoints_distance))
        else:
            self.report('No points with small gap found. iteration <{}>'.format(self.ctx.iteration))
            self.ctx.do_loop = False

    def results(self):
        calculation = self.ctx.workchain_nscf[self.ctx.iteration - 1]

        found = merge_crossing_results(
            **{'found_{}'.format(n):array for n,array in enumerate(self.ctx.found_crossings)}
            )

        n_found = len(found.get_array('crossings'))
        if self.ctx.current_kpoints_distance >= self.ctx.min_kpoints_distance and not n_found:
            self.report('No crossing found. Reached the minimum kpoints distance {}: last ran PwBaseWorkChain<{}>'.format(
                self.ctx.min_kpoints_distance, calculation.pk))
            return self.exit_codes.ERROR_MAXIMUM_ITERATIONS_EXCEEDED
        if not self.ctx.do_loop and not n_found:
            return self.exit_codes.ERROR_CANT_PINPOINT_LOWGAP_ZONE


        self.out('crossings', found)



class Z2pack3DChernWorkChain(WorkChain):
    """Workchain to compute topological invariants (Z2 or Chern number) using z2pack."""

    @classmethod
    def define(cls, spec):
        super().define(spec)

        # INPUTS ############################################################################
        spec.input(
            'structure', valid_type=orm.StructureData,
            help='The inputs structure.'
            )
        spec.input(
            'clean_workdir', valid_type=orm.Bool,
            default=orm.Bool(False),
            help='If `True`, work directories of all called calculation will be cleaned at the end of execution.'
            )

        spec.expose_inputs(
            FindCrossingsWorkChain, namespace='findc',
            exclude=('clean_workdir', 'structure'),
            namespace_options={
                'help': 'Inputs for the `FindCrossingsWorkChain`.'
                }
            )
        spec.expose_inputs(
            Z2packBaseWorkChain, namespace='z2pack',
            exclude=('clean_workdir', 'structure', 'scf'),
            namespace_options={
                'help': 'Inputs for the `FindCrossingsWorkChain`.'
                }
            )

        # OUTLINE ############################################################################
        spec.outline(
            cls.setup,
            if_(cls.should_do_find_crossings)(
                cls.run_find_crossings,
                cls.inspect_find_crossings,
            ),
            cls.run_z2pack,
            cls.inspect_z2pack,
            cls.results
            )

        # OUTPUTS ############################################################################
        # spec.output(
        #     'output_parameters', valid_type=orm.Dict,
        #     help='Dict resulting from a z2pack calculation.'
        #     )
        # spec.expose_outputs(Z2packCalculation)

        # ERRORS ############################################################################
        spec.exit_code(113, 'ERROR_SUB_PROCESS_FAILED_FINDCROSSING',
            message='the FindCrossingsWorkChain sub process failed')
        spec.exit_code(123, 'ERROR_SUB_PROCESS_FAILED_Z2PACK',
            message='the Z2packBaseWorkChain sub process failed')

    def setup(self):
        """Define the current structure in the context to be the input structure."""
        self.ctx.current_structure = self.inputs.structure
        
    def should_do_find_crossings(self):
        # """If the 'findc' input namespace was specified, we try to find band crossings."""
        return 'findc' in self.input_namespace

    def run_find_crossings(self):
        # """Run the FindCrossingsWorkChain to find bands crossings."""
        inputs = AttributeDict(self.exposed_inputs(FindCrossingsWorkChain, namespace='findc'))
        inputs.structure = self.ctx.current_structure

        running = self.submit(FindCrossingsWorkChain, **inputs)

        self.report('launching FindCrossingsWorkChain<{}>'.format(running.pk))

        return ToContext(workchain_findc=running)

    def inspect_find_crossings(self):
        # """Verify that the FindCrossingsWorkChain finished successfully."""
        workchain = self.ctx.workchain_findc

        if not workchain.is_finished_ok:
            self.report('FindCrossingsWorkChain failed with exit status {}'.format(workchain.exit_status))
            return self.exit_codes.ERROR_SUB_PROCESS_FAILED_FINDCROSSING

        # self.ctx.current_structure = workchain.outputs.output_structure
        # pass

    def run_z2pack(self):
        inputs = AttributeDict(self.exposed_inputs(Z2packBaseWorkChain, namespace='findc'))
        inputs.structure = self.ctx.current_structure

        running = self.submit(Z2packBaseWorkChain, **inputs)

        self.report('launching Z2packBaseWorkChain<{}>'.format(running.pk))

        return ToContext(workchain_z2pack=running)

    def inspect_z2pack(self):
        # """Verify that the FindCrossingsWorkChain finished successfully."""
        workchain = self.ctx.workchain_z2pack

        if not workchain.is_finished_ok:
            self.report('Z2packBaseWorkChain failed with exit status {}'.format(workchain.exit_status))
            return self.exit_codes.ERROR_SUB_PROCESS_FAILED_Z2PACK

    def results(self):
        pass

