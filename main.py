import os
from dotenv import load_dotenv

load_dotenv()  # reads .env into environment variables, if the file exists

API_TOKEN = os.environ["API_TOKEN"]
IXON_EMAIL = os.environ["IXON_EMAIL"]

# Debug
print(IXON_EMAIL)

def main():
    print("Running dts-fleet-overview main.py")

if __name__ == "__main__":
    main()