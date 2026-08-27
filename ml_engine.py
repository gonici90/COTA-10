"""Small dependency-free online ML layer for COTA-10.

Uses online logistic regression in strict walk-forward mode: predict first, then
learn after the result is known.  The final probability is deliberately
conservative because ticket construction needs calibrated probabilities, not
just a ranking score.
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
        maturity=min(1.0,(self.global_model.n+mm.n)/1200.0)
        learned=(.35*gm+.65*mp) if mm.n>=40 else gm
        raw=(1-maturity)*fallback+maturity*learned

        # Calibration guard.  The previous engine could promote a selection
        # because the model was merely confident, even when the market prior
        # disagreed.  Blend toward the implied prior as evidence matures and
        # subtract a small uncertainty margin.  This intentionally creates
        # more NO-BET days and should improve hit-rate rather than ticket count.
        implied=max(.03,min(.97,x.get('implied_p',0.0)/2.0+.5))
        evidence=min(1.0,(self.global_model.n+mm.n)/1800.0)
        calibrated=(1-.18*evidence)*raw+(.18*evidence)*implied
        uncertainty=.020-.008*evidence
        conservative=calibrated-uncertainty
        return max(.03,min(.97,conservative)), maturity

    def learn(self, market, x, won):
        y=1 if won else 0
        self.global_model.update(x,y)
        self.market_models[market].update(x,y)
