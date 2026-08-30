x = 0.0
for i in range(2000000):
    x = x * 1.0000001 + 0.5
    if x > 1e6:
        x -= 1e6
print(round(x, 3))
