import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())
import pyTigerGraph as tg
conn = tg.TigerGraphConnection(host=os.environ["TIGERGRAPH_HOST"], graphname="FraudInvestigation", username=os.environ.get("TIGERGRAPH_USER",""), password=os.environ.get("TIGERGRAPH_SECRET",""))
q = """CREATE QUERY txn_context(VERTEX<Transaction> t) FOR GRAPH FraudInvestigation {
  seed = {t};
  cards = SELECT c4 FROM seed:s-(MADE:e1)-Card:c4;
  customers = SELECT s2 FROM cards:c3-(OWNS:e2)-Customer:s2;
  devices = SELECT d4 FROM seed:s-(FROM_DEVICE:e3)-DeviceProfile:d4;
  pemail = SELECT p4 FROM seed:s-(PURCHASER_EMAIL:e4)-EmailDomain:p4;
  remail = SELECT r4 FROM seed:s-(RECIPIENT_EMAIL:e5)-EmailDomain:r4;
  region = SELECT g4 FROM seed:s-(BILLED_IN:e6)-BillingRegion:g4;
  PRINT cards, customers, devices, pemail, remail, region;
}"""
conn.gsql("USE GRAPH FraudInvestigation\nDROP QUERY txn_context")
out = conn.gsql("USE GRAPH FraudInvestigation\n" + q)
print("FULL OUTPUT:")
print(out)