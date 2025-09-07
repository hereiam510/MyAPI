# Use an official Python runtime as a parent image
# Using the slim version to keep the image size down
FROM python:3.12-slim

# Set the working directory in the container
WORKDIR /app

# Copy the dependency files into the container
COPY requirements.txt .

# Install the dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application's code into the container
COPY . .

# Expose the port the app runs on
EXPOSE 8000

# Command to run the application using uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
