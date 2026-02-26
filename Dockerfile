# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Create var directory for logs and transcriptions
RUN mkdir -p var/logs var/transcriptions var/call_data

# Declare environment variables (to be overridden at runtime)
ENV PYTHONUNBUFFERED=1
ENV CONFIG_PATH=/app/config.json
ENV SECRETS_PATH=/app/secrets.json

# The actual command will be specified in docker-compose for each service
CMD ["python", "reminder_system.py"]
