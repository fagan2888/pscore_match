"""
Implements several types of propensity score matching.
"""

from __future__ import division
import numpy as np
import scipy
from scipy.stats import binom, hypergeom, gaussian_kde, ttest_ind, ranksums
import pandas as pd
import matplotlib.pyplot as plt
import plotly
import plotly.graph_objs as go

################################################################################
################################# utils ########################################
################################################################################

def set_caliper(caliper_scale, caliper, propensity):
    # Check inputs
    if caliper_scale == None:
        caliper = 0
    if not(0<=caliper<1):
        if caliper_scale == "propensity" and caliper>1:
            raise ValueError('Caliper for "propensity" method must be between 0 and 1')
        elif caliper<0:
            raise ValueError('Caliper cannot be negative')

    # Transform the propensity scores and caliper when caliper_scale is "logit" or None
    if caliper_scale == "logit":
        propensity = np.log(propensity/(1-propensity))
        caliper = caliper*np.std(propensity)
    return caliper
    
    
def recode_groups(groups, propensity):
    # Code groups as 0 and 1
    groups = (groups == groups.unique()[0])
    N = len(groups)
    N1 = groups[groups == 1].index
    N2 = groups[groups == 0].index
    g1 = propensity[groups == 1]
    g2 = propensity[groups == 0]
    # Check if treatment groups got flipped - the smaller should correspond to N1/g1
    if len(N1) > len(N2):
       N1, N2, g1, g2 = N2, N1, g2, g1
    return groups, N1, N2, g1, g2

################################################################################
############################# Base Matching Class ##############################
################################################################################

class Match(object):
    """
    Parameters
    -----------
    groups : array-like 
        treatment assignments, must be 2 groups
    propensity : array-like 
        object containing propensity scores for each observation. 
        Propensity and groups should be in the same order (matching indices)    
    """
    
    def __init__(self, groups, propensity):
        self.groups = pd.Series(groups)
        self.propensity = pd.Series(propensity)
        assert self.groups.shape==self.propensity.shape, "Input dimensions dont match"
        assert all(self.propensity >=0) and all(self.propensity <=1), "Propensity scores must be between 0 and 1"
        assert len(np.unique(self.groups)==2), "Wrong number of groups"
        self.nobs = self.groups.shape[0]
        self.ntreat = np.sum(self.groups == 1)
        self.ncontrol = np.sum(self.groups == 0)
        
    def create(self, method='one-to-one', **kwargs):
        """
        Parameters
        -----------
        method : string
            'one-to-one' (default) or 'many-to-one'
        caliper_scale: string
            "propensity" (default) if caliper is a maximum difference in propensity scores,
            "logit" if caliper is a maximum SD of logit propensity, or "none" for no caliper
        caliper : float
             specifies maximum distance (difference in propensity scores or SD of logit propensity) 
        replace : bool
            should individuals from the larger group be allowed to match multiple individuals in the smaller group?
            (default is False)
    
        Returns
        --------
        A series containing the individuals in the control group matched to the treatment group.
        Note that with caliper matching, not every treated individual may have a match.
        """

        if method=='many-to-one':
            self._match_many(**kwargs)
            self._match_info()
        elif method=='one-to-one':
            self._match_one(**kwargs)
            self._match_info()
        else:
            raise ValueError('Invalid matching method')
            
    def _match_one(self, caliper_scale=None, caliper=0.05, replace=False):
        """
        Implements greedy one-to-one matching on propensity scores.
        """
        caliper = set_caliper(caliper_scale, caliper, self.propensity)
        groups, N1, N2, g1, g2 = recode_groups(self.groups, self.propensity)
        
        # Randomly permute the smaller group to get order for matching
        morder = np.random.permutation(N1)
        matches = {}

        for m in morder:
            dist = abs(g1[m] - g2)
            if (dist.min() <= caliper) or not caliper:
                matches[m] = dist.argmin()    # Potential problem: check for ties
                if not replace:
                    g2 = g2.drop(matches[m])
        self.matches = matches
        self.weights = np.zeros(self.nobs)
        self.freq = np.zeros(self.nobs)
        mk = list(matches.keys())
        mv = list(matches.values())
        for i in range(len(matches)):
            self.freq[mk[i]] += 1
            self.weights[mk[i]] += 1
            self.freq[mv[i]] += 1
            self.weights[mv[i]] += 1
        
        
    def _match_many(self, many_method="knn", k=1, caliper=0.05, caliper_scale="propensity", replace=True):
        ''' 
        Implements greedy one-to-many matching on propensity scores.

        Parameters
        -----------
        many_method : string
            "caliper" (default) to select all matches within a given range, "knn" for k nearest neighbors,
        k : int
            (default is 1). If method is "knn", this specifies the k in k nearest neighbors
        caliper : float
             specifies maximum distance (difference in propensity scores or SD of logit propensity) 
        caliper_scale: string
            "propensity" (default) if caliper is a maximum difference in propensity scores,
            "logit" if caliper is a maximum SD of logit propensity, or "none" for no caliper
        replace : bool
            should individuals from the larger group be allowed to match multiple individuals in the smaller group?
            (default is False)

        Returns
        --------
        A series containing the individuals in the control group matched to the treatment group.
        Note that with caliper matching, not every treated individual may have a match within calipers.
            In that case we match it to its single nearest neighbor.  The alternative is to throw out individuals with no matches, but then we'd no longer be estimating the ATT.
        '''
        if many_method=="caliper":
            assert caliper_scale is not None, "Choose a caliper"
        caliper = set_caliper(caliper_scale, caliper, self.propensity)
        groups, N1, N2, g1, g2 = recode_groups(self.groups, self.propensity)

        # Randomly permute the smaller group to get order for matching
        morder = np.random.permutation(N1)
        matches = {}

        for m in morder:
            dist = abs(g1[m] - g2)
            dist.sort_values(inplace=True)
            if many_method == "knn":
                matches[m] = np.array(dist.nsmallest(n=k).index)
            # PROBLEM: when there are ties in the knn. 
            # Need to randomly select among the observations tied for the farthest acceptable distance
            else:
                keep = np.array(dist[dist<=caliper].index)
                if len(keep):
                    matches[m] = keep
                else:
                    matches[m] = np.array([dist.argmin()])
            if not replace:
                g2 = g2.drop(matches[m])
        self.matches = matches
        self.weights = np.zeros(self.nobs)
        self.freq = np.zeros(self.nobs)
        mk = list(matches.keys())
        mv = list(matches.values())
        for i in range(len(matches)):
            self.freq[mk[i]] += 1
            self.weights[mk[i]] += 1
            self.freq[mv[i]] += 1
            self.weights[mv[i]] += 1/len(mv[i])
                

    def _match_info(self):
        '''
        Helper function to create match info
        '''
        assert self.matches is not None, 'No matches yet!'
        self.matches = {
            'match_pairs' : self.matches,
            'treated' : np.unique(list(self.matches.keys())),
            'control' : np.unique(list(self.matches.values()))
        }
        self.matches['dropped'] = np.setdiff1d(list(range(self.nobs)), 
                                    np.append(self.matches['treated'], self.matches['control']))


################################################################################
############################ helper funcs  #####################################
################################################################################

def whichMatched(matches, data, show_duplicates = True):
    ''' 
    Simple function to convert output of Matches to DataFrame of all matched observations
    
    Parameters
    -----------
    matches : Match
        Match class object with matches already fit
    data : DataFrame 
        Dataframe with unique rows, for which we want to create new matched data.
        This may be a dataframe of covariates, treatment, outcome, or any combination.
    show_duplicates : bool
        Should repeated matches be included as multiple rows? Default is True.
        If False, then duplicates appear as one row but a column of weights is
        added.
    '''
    
    if show_duplicates:
        indices = []
        for i in range(len(matches.freq)):
            j = matches.freq[i]
            while j>0:
                indices.append(i)
                j -= 1
        return data.ix[indices]
    else:
        data['weights'] = matches.weights
        data['frequency'] = matches.freq
        keep = data['frequency'] > 0
        return data.loc[keep]
        
        
################################################################################
############################ balance tests  ####################################
################################################################################
    
def rank_test(covariates, groups):
    ''' 
    Wilcoxon rank sum test for the distribution of treatment and control covariates
    
    TODO
    
    Parameters
    -----------
    covariates : DataFrame 
        Dataframe with one covariate per column.
        If matches are with replacement, then duplicates should be 
        included as additional rows.
    groups : array-like
        treatment assignments, must be 2 groups
    
    Returns
    -------
    TODO
    '''    
    colnames = list(covariates.columns)
    J = len(colnames)
    pvalues = np.zeros(J)
    for j in range(J):
        var = covariates[colnames[j]]
        res = ranksums(var[groups == 1], var[groups == 0])
        pvalues[j] = res.pvalue
    return pvalues
    
def t_test(covariates, groups):
    ''' 
    Two sample t test for the distribution of treatment and control covariates
    
    TODO
    
    Parameters
    -----------
    covariates : DataFrame 
        Dataframe with one covariate per column.
        If matches are with replacement, then duplicates should be 
        included as additional rows.
    groups : array-like
        treatment assignments, must be 2 groups
    
    Returns
    -------
    TODO
    '''
    colnames = list(covariates.columns)
    J = len(colnames)
    pvalues = np.zeros(J)
    for j in range(J):
        var = covariates[colnames[j]]
        res = ttest_ind(var[groups == 1], var[groups == 0])
        pvalues[j] = res.pvalue
    return pvalues
    



def plot_balance(matches, covariates, test=['t', 'rank']):
    '''
    Plot the p-values for covariate balance before and after matching
    '''
    
    # check valid test inputs
    test = list(test)
    extra = set(test) - set(['t', 'rank'])
    if len(extra) > 0:
        raise ValueError('unidentified test was supplied')
    
    # use matches to create covariate and tr dataframes
    covnames = list(covariates.columns)
    matched_c = whichMatched(matches, covariates, show_duplicates=True)[covnames]
    matched_g = whichMatched(matches, matches.groups, show_duplicates=True)
    
    trace0_t = None
    trace1_t = None
    trace0_rank = None
    trace1_rank = None
    # run tests, get pvalues
    if 't' in test:
        pvalues_before_t = t_test(covariates, matches.groups)
        pvalues_after_t = t_test(matched_c, matched_g)
        trace0_t = go.Scatter(
            x=pvalues_before_t,
            y=points,
            mode='markers',
            name='t-test p-values before matching',
            marker=dict(
                color='blue',
                size=12,
                symbol='circle',
            )
        )
        trace1_t = go.Scatter(
            x=pvalues_after_t,
            y=points,
            mode='markers',
            name='t-test p-values after matching',
            marker=dict(
                color='pink',
                size=12,
                symbol='circle',
            )
        )
    if 'rank' in test:
        pvalues_before_rank = rank_test(covariates, matches.groups)
        pvalues_after_rank = rank_test(matched_c, matched_g)
        trace0_rank = go.Scatter(
            x=pvalues_before_rank,
            y=points,
            mode='markers',
            name='Wilcoxon test p-values before matching',
            marker=dict(
                color='blue',
                size=12,
                symbol='triangle-up',
            )
        )
        trace1_rank = go.Scatter(
            x=pvalues_after_rank,
            y=points,
            mode='markers',
            name='Wilcoxon test p-values after matching',
            marker=dict(
                color='pink',
                size=12,
                symbol='triangle-up',
            )
        )
        
    # call code to create figure
    data = [trace0_t, trace1_t, trace0_rank, trace1_rank]
    layout = go.Layout(
        title='Balance test p-values, before and after matching',
        xaxis=dict(
            showgrid=False,
            showline=True,
            linecolor='gray',
            titlefont=dict(
                color='gray'
            ),
            tickfont=dict(
                color='gray'
            ),
            tickmode='array',
            tickvals=[0, 0.05, 0.1, 0.5, 1],
            ticktext=[0, 0.05, 0.1, 0.5, 1],
            ticks='outside',
            tickcolor='gray',
        ),
        margin=dict(
            l=140,
            r=40,
            b=50,
            t=150
        ),
        legend=dict(
            font=dict(
                size=10
            ),
            yanchor='middle',
            xanchor='right',
        ),
        width=800,
        height=600,
        hovermode='closest'
    )
    fig = go.Figure(data=data, layout=layout)
    plotly.offline.plot(fig, filename='balance-plot')