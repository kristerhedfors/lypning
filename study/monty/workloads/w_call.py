def fib(n):
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)
total = 0
for i in range(60):
    total += fib(18)
print(total)
