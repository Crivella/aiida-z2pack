# README #

# aiida-z2pack

Official Z2pack plugin for AiiDA.
This repository contains AiiDA workflows and software tools for high-throughput computational topological materials discovery, with a focus on 2D topological insulators and 3D Weyl semimetals.
The repository as of aiida-z2pack>=2.0 is compatible with aiida-core>=1.0.0. For compatibility with older versions use aiida-z2pack==1.0
The plugin supports Quantum ESPRESSO only.

### How do I get set up? ###

The Z2pack plugin has the following dependencies:
* numpy==1.16.4
* scipy==1.4.1
* sklearn==0.22.1
* z2pack==2.1.1
* aiida>=1.0.0
* aiida_quantumespresso>=3.0.0
* aiida_wannier90>=2.0.0

Installing:
* `pip install .`
* or `pip install .[dev]` to install the dependencies for developers (pre-commit, ...)

### Contribution guidelines ###

* Never commit to the master branch!
* Fork the upstream repository and create a branch from develop for the feature you want to add.
* Make a pull-request to have your changes reviewed and merged into the upstream

### Who do I talk to? ###
Contact of repository owner :
* Antimo Marrazzo (THEOS & NCCR MARVEL,EPFL), antimo.marrazzo@epfl.ch
* Davide Grassano (THEOS & NCCR MARVEL,EPFL), davide.grassano@epfl.ch
