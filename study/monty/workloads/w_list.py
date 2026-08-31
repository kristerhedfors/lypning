n = 0
xs = []
for i in range(400000):
    xs.append(i % 251)
    if len(xs) > 500:
        xs = xs[250:]
    n += xs[0]
n += sum(xs)
print(n)
