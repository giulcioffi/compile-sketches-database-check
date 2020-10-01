FROM python:3.8.5

# Copies your code file from your action repository to the filesystem path `/` of the container
COPY databasecheck /databasecheck

# Install python dependencies
#RUN pip install -r /databasecheck/requirements.txt

# Code file to execute when the docker container starts up
ENTRYPOINT ["python", "/databasecheck/databasecheck.py"]
