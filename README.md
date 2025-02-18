# Tactical workforce sizing and scheduling decisions for last-mile delivery

[![DOI](https://zenodo.org/badge/746964619.svg)](https://zenodo.org/doi/10.5281/zenodo.10561002)

This repository contains the instance generator, the instances, the solver, the solutions and the scripts used to create the figures presented in the following paper:
```bib
@article{msa2024,
    title={Tactical workforce sizing and scheduling decisions for last-mile delivery},
    author={Mandal, Minakshi Pundam and Santini, Alberto and Archetti, Claudia},
    year=2025,
    journal={European Journal of Operational Research},
    volume=323,
    issue=1,
    pages={153--159},
    doi={10.1016/j.ejor.2024.12.006},
    url={https://www.sciencedirect.com/science/article/pii/S0377221724009482}
}
```

You can also cite this repository through Zenodo:
```bib
@misc{msa2024code,
    title={{Code for the Paper ``Tactical workforce sizing and scheduling decisions for last-mile delivery''}},
    author={Mandal, Minakshi Pundam and Santini, Alberto and Archetti, Claudia},
    year=2024,
    doi={10.5281/zenodo.10561003},
    url={https://github.com/alberto-santini/lmd-sizing-scheduling}
}
```

## Instance generator

The generator is in folder `instance_generator`.
To run it, call the `main.py` script with the appropriate parameters.
The generator expects to find some geographical data in folder `data`.
However, these are very large file that are unconvenient to upload to Github (they would require the Git Large File Storage).
Therefore, you should download these files separately before running the generator.
They are:
* The "Codes Postaux" dataset with French postal codes. In particular, you need the files `codes_postaux_region.*`.
* The "PLZ 5 Stellig" dataset with German postal codes.
* The "GHS Pop Source Europe" dataset with population data for Europe. We used version R2016A.

## Already generated instances

If you only want to replicate the results of our paper, you need not use the instance generator.
Rather, pregenerated instances in JSON format are available in folder `instances`.

## Solver

The solver is a single Python script, contained in `solver/solver.py`.
It takes the following parameters:
* `-m` to specify the model name (`base`, `fixed`, `partflex`, or `flex`).
* `-i` to specify the location of the instance file.
* `-c` to specify the outsourcing cost multiplier (parameter OC in the paper).
* `-r` to specify the regional bound multiplier (parameter RM in the paper).
* `-g` to specify the global bound multiplier (parameter GM in the paper).
* `-u` to specify the maximum number of shift start times for the `partflex` model (parameter $\mu$ in the paper).
* `-o` to specify the location of the JSON solution file produced by the solver.

## Results

The solver produces one JSON file for each run.
Because we performed over 50000 runs, the total size of the results runs in a few GB.
For this reason, we publish the in condensed form, using the CSV format.
File `results/complete.csv` contains the data used in the paper.
File `results/uncapacitated.csv` contains the results of further experiments, in which we disabled the regional and global upper bound on the workforce size.

## Results analysis

The notebook `analysis/analysis.ipynb` contains a Jupyer Notebook used to perform the analysis of the results and generate the paper figures.

## License

All the code is released under the GNU Public License version 3 (GPLv3).
The license is available in file `LICENSE`.
