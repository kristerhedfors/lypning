s = "the quick brown fox jumps over the lazy dog "
n = 0
for i in range(120000):
    t = s.upper()
    n += len(t.split())
    n += s.find("fox")
    n += 1 if s.startswith("the") else 0
    u = s.replace("fox", "cat")
    n += len(u)
print(n)
