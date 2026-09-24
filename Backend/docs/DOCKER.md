# Docker Setup for Voice Agent Backend

This guide explains how to build and run the voice-agent.py application using Docker.

## Prerequisites

- Docker installed on your system
- Docker Compose (optional, for easier setup)
- Copy of `.env` file in your project root (configure with your API keys and settings)

## Quick Start with Docker Compose

1. **Your .env file already contains all necessary variables:**
   - Database credentials (MYSQL_HOST, MYSQL_USER, etc.)
   - API keys (OPENAI_API_KEY, TWILIO_ACCOUNT_SID, etc.)
   - File paths (KNOWLEDGE_FILE, INPUT_FILE, etc.)
   - External service URLs (NGROK_URL, NGURL)

2. **Build and run:**
   ```bash
   docker-compose up --build
   ```

The application will be available at `http://localhost:5050`

## Manual Docker Commands

### Build the Image

```bash
docker build -t voice-agent-backend .
```

### Run the Container (Basic)

```bash
# Mount .env file
docker run -p 5050:5050 --env-file .env voice-agent-backend
```

### Run with Volumes for Data Files

```bash
# Mount .env, data_files, dll_file, and temp directory
docker run -p 5050:5050 \
  --env-file .env \
  -v $(pwd)/data_files:/app/data_files \
  -v $(pwd)/dll_file:/app/dll_file \
  -v $(pwd)/temp:/app/temp \
  voice-agent-backend
```

### Run with Interactive Shell (for debugging)

```bash
docker run -it --env-file .env voice-agent-backend bash
```

## Environment Variables

Your .env file is loaded automatically and contains all required environment variables:

### Core Services:
- **Database**: MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
- **Twilio**: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_CALLER_ID
- **AI Services**: OPENAI_API_KEY, DEEPGRAM_API_KEY, ELEVENLABS_API_KEY
- **AWS**: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, S3_BUCKET
- **Ngrok**: NGROK_URL, NGURL, NGROK_AUTH_TOKEN

### File Paths (Docker-optimized):
- **Knowledge Base**: KNOWLEDGE_FILE=./data_files/kb.json
- **Input Data**: INPUT_FILE=./data_files/data.csv
- **Output Files**: OUTPUT_FILE=./output.json
- **FAISS Data**: FAISS_INDEX_PATH, CHUNKS_METADATA_PATH to ./data_files/
- **Excel Processing**: FILE_PATH with space-safe paths

### Docker Environment Overrides:
The docker-compose.yml includes additional environment variables for Docker compatibility:
- PYTHONPATH=/app
- Docker-specific file path overrides
- Container networking adjustments (localhost -> container svc names)

## Important Notes

1. **Data Files Volume:** Mount `data_files` to provide input data like KB files and CSVs
2. **DLL Files Volume:** Mount `dll_file` for compiled Python modules (e.g., for ML libraries)
3. **Temp Directory:** Used for temporary file uploads, mounted for persistence
4. **External Services:** Assumes MySQL database and other services are running externally
5. **GPU Support:** This Dockerfile uses CPU-only versions of PyTorch/ML libraries

## Troubleshooting
## Testing the Container

After running the container, test basic functionality:

1. **Health Check:**
   ```bash
   curl http://localhost:5050/
   ```
   Should return the HTML response with "Twilio Voicebot with Amazon Polly and Transcribe"

2. **Login Endpoint:**
   ```bash
   curl -X POST http://localhost:5050/login \
     -H "Content-Type: application/json" \
     -d '{"email":"admin@gmail.com","password":"your-password"}'
   ```

3. **Check Logs for Errors:**
   ```bash
   docker-compose logs voice-agent  # Or docker logs <container_id>
   ```

4. **Test WebSocket Connection:**
   Use a WebSocket client to connect to `ws://localhost:5050/ws`

5. **Verify Environment Variables:**
   If possible, add a debug route or check app startup logs to confirm all env vars are loaded

## Production Considerations

- Use environment-specific .env files
- Configure proper secrets management
- Set memory limits if needed: `docker run --memory 1g`
- Use health checks: `--health-cmd="curl -f http://localhost:5050/ || exit 1"`
- Monitor resource usage with tools like Prometheus

1. **Permission Issues:** Ensure mounted volumes have proper permissions
2. **Missing Environment Variables:** Check that .env contains all required keys
3. **Port Conflicts:** Change host port if 5050 is in use
4. **Build Failures:** Ensure your internet connection allows downloading large ML libraries

## Logs

To view container logs:

```bash
docker-compose logs -f  # With compose
docker logs -f <container_id>  # Manual
```

## Stop and Cleanup

```bash
docker-compose down  # With compose
docker stop <container_id> && docker rm <container_id>  # Manual