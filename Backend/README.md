# Backend Deployment

## 1. Install System Dependencies for Python

### Update package list and install required tools
```bash
sudo apt update 
sudo apt install -y python3 python3-pip python3-venv
```

## 2. Clone the Project Repository

### Create Directory
```bash
mkdir -p app/backend
cd app/backend
```

### Clone the project from your Git repository
```bash
git clone https://<YOUR_GITHUB_TOKEN>@github.com/BharatLogic-com/AI-outbound-Agent-Backend.git
cd AI-outbound-Agent-Backend
```

## 3. Set Up the Python Backend

### 3.1 Create and Activate a Virtual Environment
Navigate to backend directory and set up virtual environment
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3.2 Install Backend Dependencies
Install Python dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 3.3 Changes in .env file
Open .env file 
```bash
nano .env
```

Save env file after doing changes by:
```bash
ctrl + o 
enter
ctrl + x
```

## 4. Install MySQL Database

### Install required system packages (Ubuntu 24.04)
```bash
sudo apt-get update
sudo apt-get install -y \
  build-essential \
  pkg-config \
  python3-dev \
  default-libmysqlclient-dev
```

### Install MySQL Server
```bash
sudo apt-get update
sudo apt-get install -y mysql-server

sudo systemctl enable --now mysql
```

### Configure Database
```bash
sudo mysql
```

Run the following SQL commands:
```sql
-- Create DB (if not exists)
CREATE DATABASE IF NOT EXISTS aichat_db
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Create user with remote access
CREATE USER IF NOT EXISTS 'chatuser'@'%' IDENTIFIED BY 'SecurePass123!';

GRANT ALL PRIVILEGES ON aichat_db.* TO 'chatuser'@'%';
FLUSH PRIVILEGES;
```

### Configure MySQL for remote access
```bash
sudo nano /etc/mysql/mysql.conf.d/mysqld.cnf
```

Change the bind-address to:
```
bind-address = 0.0.0.0
```

Restart MySQL:
```bash
sudo systemctl restart mysql
```

## 5. Set Up Ngrok

### 5.1 Install Ngrok via Apt
Add Ngrok repository and install Ngrok
```bash
curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null
echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | sudo tee /etc/apt/sources.list.d/ngrok.list
sudo apt update
sudo apt install -y ngrok
```

### 5.2 Configure Ngrok Authtoken
Add your Ngrok authtoken (replace `<your-ngrok-authtoken>` with your token from ngrok.com)
```bash
ngrok config add-authtoken <your-ngrok-authtoken>
```

### To run ngrok in background with PM2:

Create a script called ngrok-start.sh:
```bash
nano ngrok-start.sh
```

Paste this inside:
```bash
ngrok http 5050
```

Save & exit (Ctrl + O, Enter, then Ctrl + X)

Make it executable:
```bash
chmod +x ngrok-start.sh
```

## 6. Install PM2

Install PM2 globally
```bash
sudo npm install -g pm2
```

## 7. Start Services

### Start Ngrok with PM2:
```bash
pm2 start ./ngrok-start.sh --name ngrok-tunnel
```

### Check if it worked:
```bash
curl http://127.0.0.1:4040/api/tunnels
```

You should now see a JSON output, copy the public url and paste it into the .env file NGROKURL and NGURL 

### Run the Backend with PM2
Start the backend (replace app.py with your entry point file)
```bash
pm2 start app.py --name backend --interpreter ./venv/bin/python
```

## 8. Useful PM2 Commands

```bash
# View all processes
pm2 list

# View logs
pm2 logs backend
pm2 logs ngrok-tunnel

# Restart services
pm2 restart backend
pm2 restart ngrok-tunnel

# Stop services
pm2 stop backend
pm2 stop ngrok-tunnel

# Save PM2 configuration
pm2 save
pm2 startup
```