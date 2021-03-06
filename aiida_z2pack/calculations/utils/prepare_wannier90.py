from __future__ import absolute_import
from aiida_wannier90.io import write_win

def prepare_wannier90(cls, folder):
    input_filename = folder.get_abs_path(cls._INPUT_W90_FILE)
    try:
        parameters = cls.inputs.wannier90_parameters.get_dict()
    except:
        parameters = {}

    for k, v in cls._blocked_keywords_wannier90:
        parameters[k] = v

    write_win(input_filename,
              parameters,
              structure=cls.inputs.structure,
              random_projections=True)
