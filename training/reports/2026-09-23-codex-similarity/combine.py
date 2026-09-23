from __future__ import annotations
import json, random, math, statistics
D=["correctness_plausibility","idiom","self_contained","subset_fit"]
key=json.load(open('/tmp/sim/key.json'))
J=[{r['id']:r for r in json.load(open(f'/tmp/sim/judge-{i}.json'))} for i in (1,2,3)]
bad=[(i,r['id'],d) for i,j in enumerate(J,1) for r in j.values() for d in D if not (isinstance(r[d],(int,float)) and 1<=r[d]<=5)]
bad+= [(i,r['id'],'guess') for i,j in enumerate(J,1) for r in j.values() if r['guess'] not in ('claude','gpt')]
assert not bad, bad
ids=sorted(key)
score={p:statistics.mean(statistics.mean(j[p][d] for d in D) for j in J) for p in ids}
pool={p:key[p]['pool'] for p in ids}
def items(*ps): return [score[p] for p in ids if pool[p] in ps]
G,C1,C2=items('G'),items('C1'),items('C2'); C=C1+C2
m=statistics.mean
out={"n":{"G":len(G),"C1":len(C1),"C2":len(C2)},
 "mean":{"G":m(G),"C1":m(C1),"C2":m(C2),"C":m(C)}}
# per-dimension and per-judge means
out["per_dim"]={d:{pl:m(m(j[p][d] for j in J) for p in ids if pool[p] in pls) for pl,pls in (("G",("G",)),("C1",("C1",)),("C2",("C2",)),("C",("C1","C2")))} for d in D}
out["per_judge"]={i+1:{pl:m(m(j[p][d] for d in D) for p in ids if pool[p] in pls) for pl,pls in (("G",("G",)),("C1",("C1",)),("C2",("C2",)),("C",("C1","C2")))} for i,j in enumerate(J)}
gGC=abs(m(G)-m(C)); g12=abs(m(C1)-m(C2))
rng=random.Random(7); bGC=[];b12=[];bd=[]
for _ in range(1000):
    g=[rng.choice(G) for _ in G]; c1=[rng.choice(C1) for _ in C1]; c2=[rng.choice(C2) for _ in C2]
    a=abs(m(g)-m(c1+c2)); b=abs(m(c1)-m(c2)); bGC.append(a); b12.append(b); bd.append(a-(b+0.25))
q=lambda xs:[round(sorted(xs)[49],4),round(sorted(xs)[949],4)]
out["B"]={"gap_G_C":gGC,"ci90":q(bGC),"gap_C1_C2":g12,"ci90_C1C2":q(b12),"threshold":g12+0.25,
 "pass":gGC<=g12+0.25,"boot_share_gap_exceeds_threshold":sum(x>0 for x in bd)/1000,
 "signed_G_minus_C":m(G)-m(C)}
# C
def binom_p(k,n):  # one-sided P(X>=k), p=.5
    return sum(math.comb(n,i) for i in range(k,n+1))/2**n
claude_ids=[p for p in ids if pool[p]!='G']; g_ids=[p for p in ids if pool[p]=='G']
sub=random.Random(5).sample(claude_ids,len(g_ids)); bal=g_ids+sub
truth={p:('gpt' if pool[p]=='G' else 'claude') for p in ids}
res={}
for i,j in enumerate(J,1):
    k=sum(j[p]['guess']==truth[p] for p in bal); res[f"judge{i}"]={"correct":k,"n":len(bal),"acc":k/len(bal),"p_one_sided":binom_p(k,len(bal)),
      "G_recall":sum(j[p]['guess']=='gpt' for p in g_ids)/len(g_ids),"claude_subset_recall":sum(j[p]['guess']=='claude' for p in sub)/len(sub)}
maj={p:('gpt' if sum(j[p]['guess']=='gpt' for j in J)>=2 else 'claude') for p in ids}
k=sum(maj[p]==truth[p] for p in bal); res["majority_vote(primary)"]={"correct":k,"n":len(bal),"acc":k/len(bal),"p_one_sided":binom_p(k,len(bal)),
  "G_recall":sum(maj[p]=='gpt' for p in g_ids)/len(g_ids),"claude_subset_recall":sum(maj[p]=='claude' for p in sub)/len(sub)}
kp=sum(j[p]['guess']==truth[p] for j in J for p in bal); res["pooled_78_guesses(not independent)"]={"correct":kp,"n":3*len(bal),"acc":kp/(3*len(bal)),"p_one_sided":binom_p(kp,3*len(bal))}
# informational: all 77 items, balanced accuracy
for name,g in [("judge%d"%(i+1),{p:J[i][p]['guess'] for p in ids}) for i in range(3)]+[("majority",maj)]:
    tpr=sum(g[p]=='gpt' for p in g_ids)/len(g_ids); tnr=sum(g[p]=='claude' for p in claude_ids)/len(claude_ids)
    res.setdefault("all77_balanced_accuracy",{})[name]=round((tpr+tnr)/2,4)
# informational: 'gpt' guess rate by Claude pool
res["gpt_guess_rate_by_pool_majority"]={pl:sum(maj[p]=='gpt' for p in ids if pool[p]==pl)/sum(pool[p]==pl for p in ids) for pl in ('G','C1','C2')}
pm=res["majority_vote(primary)"]
res["pass"]= (pm["p_one_sided"]>=0.05) or (pm["acc"]<=0.60)
out["C"]=res
out["C_min_correct_for_significance_n26"]=min(k for k in range(27) if binom_p(k,26)<0.05)
json.dump(out,open('/tmp/sim/combine_result.json','w'),indent=1)
print(json.dumps(out,indent=1))
