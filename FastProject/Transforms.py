# -*- coding: utf-8 -*-
"""Functions to transform the data and calculate weights

Originally 'transform' referred to going from 
    ExpressionData -> ProbabilityData

"""
;


from .Utils import em_exp_norm_mixture;
from . import Filters;
from .DataTypes import ExpressionData, ProbabilityData, PCData;
import numpy as np;
import os;

def probability_of_expression(data):
    cutoffs = np.mean(data,axis=1)/4;  #Empirically found to be good most of the time
    
    (gamma, mu_l, mu_h, st_l, st_h, Pi, L) = em_exp_norm_mixture(data,cutoffs);
    
    gamma = make_monotonic(gamma, data);    
    
    return (gamma, mu_h, mu_l, st_h, Pi);

def make_monotonic(gamma, data):
    """For each row in gamma, finds the first corresponding value in data
    in which gamma hits it's maximum.  Then, for all values of data > than that
    value, set gamma = gamma_max
    
    Dimensions of Gamma should be equal to Dimensions of Data"""
    
    for i in np.arange(gamma.shape[0]):
        max_g = np.max(gamma[i,:]);
        locs = np.flatnonzero(gamma[i,:] == max_g);
        max_d = data[i,locs[0]];
        
        locs_to_change = data[i,:] > max_d;
        gamma[i,locs_to_change] = max_g;

    return gamma;        
    

def create_false_neg_map(data, housekeeping_genes, debug=None):
    """Uses gene names in `housekeeping_genes` to create a mapping of false negatives.
        
    Creates a functional fit for each sample based on that samples HK genes

    debug (if supplied), should be an int representing a particular sample
        that should be examined

    Returns
    ----------
    fit_func : function 
        Used to fit expression values to FN rate
    params : (Num_Params x Num_Samples) numpy.ndarray
        Sample-specific parameters to use with fit_func

    """
    
    keep_indices = list();

    for hkgene in housekeeping_genes:
        for i, gene in enumerate(data.row_labels):
            if(gene.upper() == hkgene.upper()):
                keep_indices.append(i);
                continue;

    data_hk = data.subset_genes(keep_indices);


    data_hk = Filters.filter_genes_novar(data_hk);
        
    #calculate distributions for hk gene
    # Gamma is 1 for any non-zero data point
    # Mu_h is the row (per gene) average of non-zero points
    gamma = (data_hk > 0).astype('float');
    count_nonzero = (data_hk != 0).sum(axis=1);
    mu_h = data_hk.sum(axis=1) / count_nonzero;

    #Fit a function mapping mu to gammas

    def func(xvals, x0, a, L=0, S=1):
        return L + S/(1 + np.exp((xvals-x0)*a));

    def efun(x,y, args):
        out = func(x, args[0], args[1]);
        return np.sum((out-y)**2);

    params = np.zeros((4,gamma.shape[1]));
    x = mu_h.flatten();
    


    if(len(x) > 30):
        q_indices = np.round(len(x)/30 * np.arange(30));
    else:
        q_indices = np.arange(30);

    q_indices = np.append(q_indices, len(x));
    q_indices = q_indices.astype(np.int64);

    sort_i = np.argsort(x);
    x_sorted = x[sort_i];

    y = 1-gamma;
    y_sorted = y[sort_i,:]

    x_quant = np.zeros(len(q_indices)-1);
    y_quant = np.zeros((len(q_indices)-1, y.shape[1]))

    for i in range(len(q_indices)-1):
        start_i = q_indices[i];
        end_i = q_indices[i+1];

        x_quant[i] = np.mean(x_sorted[start_i:end_i]);
        y_quant[i,:] = np.mean(y_sorted[start_i:end_i,:], axis = 0);

    from scipy.optimize import minimize;

    #Multiple restarts for better solutions
    initial_guesses = [[3.5, 1],
                       [5.5, 1],
                       [1.5, .5],
                       [5.5, .5],
                       [3.5, 1.7]];

    bounds = [(0, np.inf),(0, 2)];

    for i in range(gamma.shape[1]):
        best_eval = 1e99;
        for initial_guess in initial_guesses:
            res = minimize(lambda args: efun(x_quant,y_quant[:,i],args), initial_guess, bounds=bounds);
            if(res.fun < best_eval):
                best_eval = res.fun;
                param = res.x;
                params[0:2, i] = param;
                params[2, i] = 0;
                params[3, i] = 1;

    if(debug is not None):
        import matplotlib.pyplot as plt;
        i = debug;

        plt.close();
        domain = np.linspace(0,10,1000);
        plt.plot(x,y[:,i], 'o');
        plt.plot(x_quant, y_quant[:,i], 'o', color='red')
        plt.plot(domain, func(domain, params[0,i], params[1,i], params[2,i], params[3,i]));
        plt.ylabel("P(gene not expressed in " + data_hk.col_labels[i] + ")");
        plt.xlabel("Gene average in samples expressing gene")
        print(params[:,i])

        
    return func, params;

def quality_check(params):
    """Integrates the logistic false-negative curves.  Flags samples whose 
    integral is more than 1.6 MAD lower than the population.  
    
    Parameters
    ----------
    params : (4 x Num_Samples) numpy.ndarray 
        Matrix containing parameters for the false-negative fit function

    Returns
    -------
    sample_passes : (Num_Samples) boolean numpy.ndarray
        Vector containing True for samples that pass this quality check
    sample_score : (Num_Samples) float numpy.ndarray
          Vector containing a score representing the quality of each sample.
          Smaller is better.
    """
    
    #Logistic parameters
    x0 = params[0,:];
    a = params[1,:];
    L = params[2,:];
    S = params[3,:];

    #Bounds of integration
    low = 0;
    high = 9;
    
    a[a == 0] = 1e-6;  #Fix so that integral isn't mis-calculated as inf or nan
    
    #Evaluate integral
    int_low = (L+S)*low   - S/a * np.log(np.exp(a*(low -x0)) + 1)
    int_high = (L+S)*high - S/a * np.log(np.exp(a*(high-x0)) + 1)
    
    int_val = int_high - int_low;

    #Invert integral QC score increases with increasing quality
    int_val = (high-low) - int_val;
    int_val_med = np.median(int_val);
    
    abs_dev = np.abs(int_val - int_val_med);
    
    MAD = np.median(abs_dev);
    
    sample_passes = int_val >= (int_val_med - 1.6*MAD);

    sample_score = int_val;
    
    return sample_passes, sample_score;
    

def plot_em_norm_distribution(gamma, mu_l, mu_h, st_l, st_h, data, i):

    mu_lx = mu_l[i];
    mu_hx = mu_h[i];
    st_hx = st_h[i];
    
    domain = np.linspace(0,10,10000);
    p_low = np.exp(-1*domain/mu_lx)/mu_lx;
    p_low[np.isnan(p_low)] = 0;    
    
    p_high = np.exp(-1 * (domain - mu_hx)**2 / (2*st_hx**2)) / st_hx / np.sqrt(2*np.pi);
    
    from matplotlib.pyplot import hold, hist, plot, scatter, ylim    
    
    hold(False);
    (n, bins, patches) = hist(data[i,:], range=(0,10),bins=100, normed=True);
    hold(True);    
    plot(domain, p_low, color='green');
    plot(domain, p_high, color='green');
    scatter(data[i,:], gamma[i,:], color='red');
    ylim(0, 1.1);    


def adjust_pdata(prob, weights):
    """
    Uses the estimated weights to modify pdata values

    weights represent p(not expressed | not detected)

    Parameters
    ----------
    prob : (Num_Genes x Num_Samples) numpy.ndarray
        Matrix containing estimate for probability of expression of each gene in each sample
    weights : (Num_Genes x Num_Samples) numpy.ndarray
        Matrix containing estimate for p(not expressed | not detected) for each data point

    Returns
    -------
    out_prob : (Num_Genes x Num_Samples) numpy.ndarray    
        Adjusted probability values
    """

    out_prob = prob + (1 - prob) * (1 - weights);

    return out_prob;


def compute_weights(fit_func, params, data):
    """
    Calculates weights for the data from the FNR curves

    Weights represent p(not expressed | not detected) for zero values
        and are equal to 1.0 for detected values.

    Parameters
    ----------
    fit_func : function (mu_h, params)
        Function, parameterized by params, that maps each mu_h to a false negative estimate
    params : (4 x Num_Samples) numpy.ndarray
        Matrix containing parameters for the false-negative fit function (fit_func)
    data : ExpressionData object from which prob derives

    Returns
    -------
    weights : (Num_Genes x Num_Samples) numpy.ndarray
        Estimated weight for each data point in input matrix.
        Ranges from 0 to 1.
    """

    fn_prob = np.zeros(data.shape)
    count_nonzero = (data.base > 0).sum(axis=1);
    count_nonzero[count_nonzero == 0] = 1;  # Protect agains NaN
    mu_h = data.base.sum(axis=1) / count_nonzero;

    for i in range(fn_prob.shape[1]):
        fn_prob[:, i] = fit_func(mu_h, *params[:, i]).ravel();

    pd_e = 1 - fn_prob;

    pnd = (data == 0).sum(axis=1, keepdims=True) / data.shape[1];
    pe = (1 - pnd) / (pd_e).mean(axis=1, keepdims=True);

    pe[np.isnan(pe)] == 1.0;  # Set to 1 if all expressed

    pnd[pnd == 0] = 1.0 / data.shape[1] # For stability

    pne_nd = 1 - (1 - pd_e) * pe / pnd;

    pne_nd[pne_nd < 0] = 0.0;
    pne_nd[pne_nd > 1] = 1.0;

    weights = pne_nd;
    weights[data > 0] = 1.0;

    return weights;

#def utility_plotting_routine(i, cutoff):
#    #cutoff = 5;
#    #i = 1;
#    vals = data[i,:];
#    vals = vals[vals != 0];
#    
#    (gamma, mu_l, mu_h, st_l, st_h, Pi, L) = em.em_exp_norm_mixture(vals,cutoff);
#    mu_l.shape = 1;
#    mu_h.shape = 1;
#    st_h.shape = 1;
#    
#    domain = np.linspace(0,10,10000);
#    p_low = exp(-1*domain/mu_l)/mu_l;
#    
#    p_high = exp(-1 * (domain - mu_h)**2 / (2*st_h**2)) / st_h / np.sqrt(2*np.pi);
#    
#    
#    f = figure();
#    (n, bins, patches) = hist(vals, range=(0,10),bins=100, normed=True);
#    plot(domain, p_low, color='green');
#    plot(domain, p_high, color='green');
#    scatter(vals, gamma, color='red');
#    ylim(0, 1.1);
#    
#    display(f)

def z_normalize(data):
    """
    Z-normalizes the rows of the matrix in data.
    No return as operation is done in place.
    Mean is subtracted out result is scaled so standard deviation = 1
    :param data: numpy.ndarray, 2 dimensions
    :return: None
    """
    if(data is ProbabilityData or data is PCData):
        raise TypeError("Should not be z-normalizing Probability or PCData, exiting");

    mu = data.mean(axis=1, keepdims=True);
    sigma = data.std(axis=1, keepdims=True);

    #Shouldn't be necessary for expression data that's been filtered, but good practice
    sigma[sigma == 0] = 1;

    #Note, operations are in place, no return needed
    data -= mu;
    data /= sigma;
