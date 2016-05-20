#!/usr/bin/env python

# Load required modules
import sys, os, argparse, numpy as np, json
from itertools import combinations
from collections import defaultdict
from time import time

# Load the weighted exclusivity test, ensuring that
# it is in the path (unless this script was moved)
sys.path.append(os.path.dirname(os.path.realpath(__file__)))
from weighted_exclusivity_test import *

# Argument parser
def get_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument('-mf', '--mutation_files', type=str, required=True, nargs='*')
    parser.add_argument('-o', '--output_prefix', type=str, required=True)
    parser.add_argument('-f', '--min_frequency', type=int, default=1, required=False)
    parser.add_argument('-c', '--num_cores', type=int, required=False, default=1)
    parser.add_argument('-v', '--verbose', type=int, required=False, default=1, choices=range(5))
    parser.add_argument('--json_format', action='store_true', default=False, required=False)

    # Search strategy
    parser.add_argument('-s', '--search_strategy', type=str, choices=['Enumerate', 'MCMC'], default='MCMC', required=False)
    parser.add_argument('-ks', '--gene_set_sizes', nargs="*", type=int, required=True, choices=SET_SIZES_IMPLEMENTED)
    parser.add_argument('-N', '--num_iterations', type=int, default=pow(10, 3))
    parser.add_argument('-nc', '--num_chains', type=int, default=1)
    parser.add_argument('-sl', '--step_length', type=int, default=100)
    parser.add_argument('-a', '--alpha', type=float, default=2., required=False, help='CoMEt parameter alpha.')
    parser.add_argument('--mcmc_seed', type=int, default=int(time()), required=False)

    # Subparser: statistical test
    subparser1 = parser.add_subparsers(dest='test', help='Type of test')

    permutational_parser = subparser1.add_parser("Permutational")
    permutational_parser.add_argument('-np', '--num_permutations', type=int, required=True)
    permutational_parser.add_argument('-pf', '--permuted_matrix_files', type=str, nargs='*', required=True)

    weighted_parser = subparser1.add_parser("Weighted")
    weighted_parser.add_argument('-m', '--method', choices=METHOD_NAMES, type=str, required=True)
    weighted_parser.add_argument('-wf', '--weights_file', type=str, required=True)

    unweighted_parser = subparser1.add_parser("Unweighted")
    unweighted_parser.add_argument('-m', '--method', choices=METHOD_NAMES, type=str, required=True)

    return parser

def get_permuted_files(permuted_matrix_directories, num_permutations):
    # Group and restrict the list of files we're testing
    permuted_directory_files = []
    for permuted_matrix_dir in permuted_matrix_directories:
        files = os.listdir(permuted_matrix_dir)
        permuted_matrices = [ '{}/{}'.format(permuted_matrix_dir, f) for f in files ]
        permuted_directory_files.append( permuted_matrices[:num_permutations] )
    assert( len(files) == num_permutations for files in permuted_directory_files )

    return zip(*permuted_directory_files)

def load_weight_files(weights_files, m, n, typeToGenes, typeToGeneIndex, geneToIndex):
    # Master matrix of all weights
    P = np.zeros((m, n))
    patient_index = 0
    for i, weights_file in enumerate(weights_files):
        # Load the weights matrix for this cancer type and update the entires appropriately
        type_P          = np.load(weights_file)
        ty_num_patients = np.shape(type_P)[1]
        ty_genes        = set(typeToGenes[i]) & genes
        ty_gene_indices = [ typeToGeneIndex[i][g] for g in ty_genes ]
        gene_indices    = [ geneToIndex[g] for g in ty_genes ]
        P[gene_indices, patient_index:patient_index + ty_num_patients] = type_P[ ty_gene_indices ]
        patient_index  += ty_num_patients

    # Set any zero entries to the minimum (pseudocount)
    P[P == 0] = np.min(P[P > 0])
    return dict( (g, P[geneToIndex[g]]) for g in genes )

def run( args ):
    # Provide additional checks on arguments
    if args.test == 'Permutational':
        assert( len(args.mutation_files) == len(args.permuted_matrix_directories ) )
        assert( args.strategy != "MCMC" ) # MCMC is not implemented for permutational
    elif args.test == 'Weighted':
        assert( len(args.mutation_files) == len(args.weights_files) )

    # Load the mutation data
    if args.verbose > 0:
        print '* Loading mutation data...'

    typeToGeneIndex = []
    genes, patients, geneToCases, typeToGenes = set(), set(), defaultdict(set), []
    for i, mutation_file in enumerate(args.mutation_files):
        # Load ALL the data, we restrict by mutation frequency later
        mutation_data = load_mutation_data( mutation_file, 0 )
        _, type_genes, type_patients, typeGeneToCases, _, params, _ = mutation_data

        # We take the union of all patients and genes
        patients |= set(type_patients)
        genes    |= set(type_genes)

        # Record the mutations in each gene
        for g, cases in typeGeneToCases.iteritems(): geneToCases[g] |= cases

        # Record the index and genes for later
        typeToGenes.append( type_genes )
        typeToGeneIndex.append(dict(zip(type_genes, range(len(type_genes)))))

    num_all_genes, num_patients = len(genes), len(patients)

    # Restrict to genes mutated in a minimum number of samples
    geneToCases = dict( (g, cases) for g, cases in geneToCases.iteritems() if g in genes and len(cases) >= args.min_frequency )
    genes     = set(geneToCases.keys())
    num_genes = len(genes)
    geneToIndex  = dict(zip(sorted(genes), range(num_genes)))
    gene_indices = [ geneToIndex[g] for g in genes ]

    if args.verbose > 0:
        print '\tGenes:', num_all_genes
        print '\tPatients:', num_patients
        print '\tGenes mutated in >={} patients: {}'.format(args.min_frequency, num_genes)

    # Load the weights (if necessary)
    test = nameToTest[args.test]
    if test == WEIGHTED:
        geneToP = load_weight_files(weights_files, num_genes, num_patients, typeToGenes, typeToGeneIndex, geneToIndex)
    else:
        geneToP = None

    # Find the permuted matrices (if necessary)
    if test == PERMUTATIONAL:
        permuted_files = get_permuted_files(args.permuted_matrix_directories, args.num_permutations)
        if args.verbose > 0:
            print '* Using {} permuted matrix files'.format(len(permuted_files))

    # Search for exclusive sets
    method = nameToMethod[args.method]

    #Enumeration
    if args.search_strategy == 'Enumerate':
        # Create a list of sets to test
        sets = list( frozenset(t) for t in combinations(genes, args.gene_set_sizes[0]) )
        num_sets = len(sets)

        if args.verbose  > 0: print '* Testing {} sets...'.format(num_sets)
        if test == PERMUTATIONAL:
            # Run the permutational
            setToPval, setToRuntime, setToFDR, setToObs = permutational_test( sets, geneToCases, num_patients, permuted_files, args.num_cores, args.verbose )
        else:
            # Run the test
            method = nameToMethod[args.method]
            setToPval, setToRuntime, setToFDR, setToObs = test_sets(sets, geneToCases, num_patients, method, test, geneToP, args.num_cores, args.verbose)
        output_enumeration_table( args, setToPval, setToRuntime, setToFDR, setToObs )

    # MCMC
    elif args.search_strategy == 'MCMC':
        mcmc_params = dict(niters=args.num_iterations, nchains=args.num_chains, step_len=args.step_length, verbose=args.verbose, seed=args.mcmc_seed)
        setsToFreq, setToPval, setToObs = mcmc(args.gene_set_sizes, geneToCases, num_patients, method, test, geneToP, **mcmc_params)
        output_mcmc(args, setsToFreq, setToPval, setToObs)
    else:
        raise NotImplementedError("Strategy '{}' not implemented.".format(args.strategy))

if __name__ == '__main__': run( get_parser().parse_args(sys.argv[1:]) )
