"""Small dependency-free online ML layer for COTA-10.

Uses online logistic regression. It is deliberately walk-forward: predict first,
then learn after the result is known, so historical backtests cannot see future
results. The learner can be fed every historical market observation and keeps
separate models by market plus a global model.
"""
import math
from collections import defaultdict


def _sigmoid(z):
    z=max(-30.0,min(30.0,z))
    return 1.0/(1.0+math.exp(-z))


class OnlineLogistic:
    def __init__(self, lr=.035, l2=.0008):
        self.lr=lr; self.l2=l2; self.w=defaultdict(float); self.n=0

    def predict(self, x):
        z=self.w['bias']
        for k,v in x.items(): z += self.w[k]*v
        return _sigmoid(z)

    def update(self, x, y):
        p=self.predict(x); err=float(y)-p
        rate=self.lr/math.sqrt(1.0+self.n/250.0)
        self.w['bias'] += rate*err
        for k,v in x.items():
            self.w[k] += rate*(err*v-self.l2*self.w[k])
        self.n += 1
        return p


class WalkForwardLearner:
    def __init__(self):
        self.global_model=OnlineLogistic(lr=.025)
        self.market_models=defaultdict(lambda:OnlineLogistic(lr=.04))

    @staticmethod
    def features(model_p, odds, league, market, hs=None, aws=None):
        imp=1.0/max(1.01,float(odds))
        x={
            'model_p':(model_p-.5)*2,
            'implied_p':(imp-.5)*2,
            'edge':max(-.35,min(.35,model_p-imp))*3,
            'log_odds':max(0.,min(1.5,math.log(max(1.01,odds))))/1.5,
            'league:'+league:1.0,
        }
        if hs and aws:
            x['home_gf']=max(-1.,min(1.,(hs.get('gf',1.4)-1.4)/1.4))
            x['home_ga']=max(-1.,min(1.,(hs.get('ga',1.4)-1.4)/1.4))
            x['away_gf']=max(-1.,min(1.,(aws.get('gf',1.2)-1.2)/1.4))
            x['away_ga']=max(-1.,min(1.,(aws.get('ga',1.4)-1.4)/1.4))
        return x

    def predict(self, market, x, fallback):
        gm=self.global_model.predict(x)
        mm=self.market_models[market]
        mp=mm.predict(x)
        # Cold-start protection: the old calibrated probability dominates until
        # the learner has accumulated a meaningful amount of prior evidence.
        maturity=min(1.0,(self.global_model.n+mm.n)/1200.0)
        learned=(.35*gm+.65*mp) if mm.n>=40 else gm
        return max(.03,min(.97,(1-maturity)*fallback+maturity*learned)), maturity

    def learn(self, market, x, won):
        y=1 if won else 0
        self.global_model.update(x,y)
        self.market_models[market].update(x,y)
