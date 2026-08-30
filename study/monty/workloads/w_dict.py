d = {}
n = 0
for i in range(600000):
    k = i % 1000
    d[k] = d.get(k, 0) + 1
    n += d[k]
print(n, len(d))
