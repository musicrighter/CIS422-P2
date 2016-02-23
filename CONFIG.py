"""
Configuration of app. 
Edit to fit development or deployment environment.

"""
import random 

### My local development environment
#PORT=5000
#DEBUG = True


### On ix.cs.uoregon.edu
PORT=5678
DEBUG = False # Because it's unsafe to run outside localhost
GOOGLE_LICENSE_KEY = ".goog_app_key.json"
MONGO_URL = "mongodb://meet:propose@ix.cs.uoregon.edu:4260/meetings"