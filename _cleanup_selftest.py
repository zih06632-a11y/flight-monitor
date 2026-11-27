import os
import sqlite3
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import store
con = store.connect()
n1 = con.execute("DELETE FROM offers WHERE platform='selftest'").rowcount
n2 = con.execute("DELETE FROM runs WHERE platform='selftest'").rowcount
n3 = con.execute("DELETE FROM alerts WHERE key LIKE '%selftest%'").rowcount
con.commit()
left = con.execute("SELECT platform, COUNT(*) FROM offers GROUP BY platform").fetchall()
con.close()
print("deleted offers=%d runs=%d alerts=%d" % (n1, n2, n3))
print("remaining offers by platform:", left)
