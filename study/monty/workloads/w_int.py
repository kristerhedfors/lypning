n = 0
for i in range(3000000):
    n = (n + i * 7) % 999983
print(n)
