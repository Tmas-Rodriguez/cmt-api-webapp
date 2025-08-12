import sys
import subprocess

# c:/Users/Tomas/Documents/ESG/cmt-api/EsgCmt-API/gera.py

if len(sys.argv) < 2:
    print("No company name provided.")
    sys.exit(1)

company_name = sys.argv[1]
print(f"You selected: {company_name}")