#!/bin/bash

echo "Running compile check..."
python -m compileall .

echo "Running API..."
uvicorn api_server:app --reload &

sleep 3

echo "Health check..."
curl http://localhost:8000/health

echo "DONE"
