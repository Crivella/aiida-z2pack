import copy
from aiida import orm
from aiida.common import datastructures

from aiida_quantumespresso.utils.resources import get_default_options

def test_z2pack_inputs(
    aiida_profile, fixture_sandbox, generate_calc_job, fixture_code, generate_structure, generate_kpoints_mesh,
    generate_upf_data, file_regression
):
    """Test a default `PwCalculation`."""
    entry_point_name = 'z2pack'

    parameters = {'CONTROL': {'calculation': 'scf'}, 'SYSTEM': {'ecutrho': 240.0, 'ecutwfc': 30.0}}
    nscf_parameters = copy.deepcopy(parameters)
    nscf_parameters['CONTROL']['calculation'] = 'nscf'

    upf = generate_upf_data('Si')
    inputs = {
        'code': fixture_code(entry_point_name),
        'structure': generate_structure(),
        'kpoints': generate_kpoints_mesh(2),
        'parameters': orm.Dict(dict=parameters),
        'nscf_parameters': orm.Dict(dict=nscf_parameters),
        'wannier90_parameters': orm.Dict(dict={}),
        'nscf_code': fixture_code('quantumespresso.pw'),
        'overlap_code': fixture_code('quantumespresso.pw2wannier90'),
        # 'wannier90_code': fixture_code('quantumespresso.pw'),
        'pseudos': {
            'Si': upf
        },
        'metadata': {
            'options': get_default_options()
        }
    }

    print(inputs)

    calc_info = generate_calc_job(fixture_sandbox, entry_point_name, inputs)

    cmdline_params = ['-in', 'aiida.in']
    local_copy_list = [(upf.uuid, upf.filename, u'./pseudo/Si.upf')]
    retrieve_list = ['aiida.out', './out/aiida.save/data-file-schema.xml', './out/aiida.save/data-file.xml']
    retrieve_temporary_list = [['./out/aiida.save/K*[0-9]/eigenval*.xml', '.', 2]]

    # Check the attributes of the returned `CalcInfo`
    assert isinstance(calc_info, datastructures.CalcInfo)
    assert sorted(calc_info.cmdline_params) == sorted(cmdline_params)
    assert sorted(calc_info.local_copy_list) == sorted(local_copy_list)
    assert sorted(calc_info.retrieve_list) == sorted(retrieve_list)
    assert sorted(calc_info.retrieve_temporary_list) == sorted(retrieve_temporary_list)
    assert sorted(calc_info.remote_symlink_list) == sorted([])

    with fixture_sandbox.open('aiida.in') as handle:
        input_written = handle.read()

    # Checks on the files written to the sandbox folder as raw input
    assert sorted(fixture_sandbox.get_content_list()) == sorted(['aiida.in', 'pseudo', 'out'])
    file_regression.check(input_written, encoding='utf-8', extension='.in')