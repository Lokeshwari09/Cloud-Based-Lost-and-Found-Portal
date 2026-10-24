# Cloud-Based Lost and Found Portal

A cloud-ready web portal where users report **lost** and **found** items, search the board, get **automatic match suggestions**, and submit **ownership claims** that an admin verifies. Built with Flask + SQLite, containerised with Docker, deployable on AWS, and capacity-planned with CloudSim.

## Project structure

```
lost-and-found-portal/
├── app/
│   ├── app.py              # Flask application (routes, DB, matching engine, API)
│   ├── requirements.txt
│   └── templates/          # HTML pages (board, report, detail, admin...)
├── cloudsim/
│   ├── src/LostFoundSimulation.java   # CloudSim capacity simulation
│   ├── lib/                # put cloudsim-3.0.3.jar here
│   └── run.bat / run.sh
├── Dockerfile
├── docker-compose.yml
└── .vscode/launch.json     # press F5 in VS Code to run
```

## Features

| Feature | Description |
|---|---|
| Report lost / found | Form with category, location, date, description, optional photo |
| Search and filter | By keyword, lost/found, category, status |
| Smart matching | Token-similarity engine suggests found items for a lost item and vice versa |
| Claim workflow | Claimant submits proof, item becomes `claim_pending`, admin approves (item `returned`) or rejects |
| Admin dashboard | Stats (total, lost, found, returned, pending claims, recovery rate) and claim management |
| REST API | `GET /api/items`, `GET /api/stats` (personal data is never exposed) |
| Health check | `GET /health` used by the AWS load balancer |
| Cloud storage | Set `S3_BUCKET` and photos go to AWS S3 instead of local disk |

---

## 1. Run in VS Code (quickest)

Requirements: Python 3.9+ and VS Code with the Python extension.

Open the `lost-and-found-portal` folder in VS Code, open a terminal (`Ctrl + ~`) and run:

```bash
cd app
python -m venv .venv
# Windows:   .venv\Scripts\activate
# Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open **http://localhost:5000**. (Or press **F5** in VS Code to start with the included launch config.)

- Public site: `/`
- Admin login: `/admin/login`, password `admin123`
- API: `/api/items`, `/api/stats`, `/health`

Sample data is loaded on first run so the board and matching work immediately
(the "Black leather wallet" lost report matches the "Black wallet near library" found report).

---

## 2. Run with Docker

Requirement: Docker Desktop.

```bash
docker compose up --build
```

Open http://localhost:5000. Data and uploads persist in the `portal-data` volume.
Stop with `Ctrl + C` or `docker compose down`.

Useful commands:

```bash
docker ps                          # running containers
docker logs lost-found-portal      # app logs
docker compose down -v             # stop and delete data volume
```

---

## 3. Deploy on AWS (console steps)

### Option A: Single EC2 server (simplest)

1. **EC2 > Launch instance**: Amazon Linux 2023, `t3.micro` / `t2.micro` (free tier), create a key pair.
2. **Security group**: allow SSH (22) from your IP and Custom TCP **5000** (or HTTP 80) from anywhere.
3. Connect and install Docker:
   ```bash
   sudo dnf install -y docker git
   sudo systemctl enable --now docker
   sudo usermod -aG docker ec2-user && newgrp docker
   sudo curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 -o /usr/local/bin/docker-compose
   sudo chmod +x /usr/local/bin/docker-compose
   ```
4. Copy the project (WinSCP/`scp`, or push to GitHub and `git clone`), then:
   ```bash
   cd lost-and-found-portal
   docker-compose up -d --build
   ```
5. Visit `http://<EC2-public-IP>:5000`.

### Option B: Photos in S3 (cloud storage)

1. **S3 > Create bucket** (for example `lostfound-photos-<yourname>`), keep "Block all public access" ON (the app uses presigned URLs).
2. **IAM > Roles > Create role** for EC2 with a policy allowing `s3:PutObject` and `s3:GetObject` on `arn:aws:s3:::<bucket>/*`. Attach it to the EC2 instance (Actions > Security > Modify IAM role).
3. In `docker-compose.yml` set `S3_BUCKET` and `AWS_REGION` (no access keys needed when the IAM role is attached), then `docker-compose up -d --build`.
4. Admin dashboard now shows storage backend **aws-s3**.

### Option C: Highly available (for the report / viva diagram)

```
Users -> Route 53 -> Application Load Balancer (health check: /health)
                          |-> EC2 (Docker) in AZ-a  \
                          |-> EC2 (Docker) in AZ-b   > Auto Scaling Group (min 2, max 4, CPU > 60%)
                                     |
                           S3 (photos) + RDS / EFS (database)
```

Steps: create a Launch Template (user data installs Docker and runs the container), an Auto Scaling Group across two AZs, an Application Load Balancer with target-group health check path `/health`, and CloudWatch alarms for CPU.
For multiple servers, move SQLite to **Amazon RDS** (MySQL/PostgreSQL); the CloudSim result below justifies the 2 to 4 server range.

---

## 4. Run on Oracle VirtualBox (optional)

1. Create an Ubuntu Server VM (2 GB RAM, 20 GB disk). Network: **Bridged** (or NAT with port forwarding host 5000 > guest 5000).
2. Inside the VM:
   ```bash
   sudo apt update && sudo apt install -y docker.io docker-compose-v2
   sudo usermod -aG docker $USER && newgrp docker
   # copy the project into the VM (shared folder / scp), then:
   docker compose up -d --build
   ```
3. Open `http://<VM-IP>:5000` from the host browser.

---

## 5. CloudSim simulation (capacity planning)

Goal: find how many web-server VMs the portal needs. The simulation runs the same workload of **60 requests** (search, report item, upload photo, run matching) on **1, 2 and 4 VMs** in a simulated AWS Mumbai datacenter and compares response time and cost.

Requirements: JDK 8 or newer, and `cloudsim-3.0.3.jar` (download from the CloudSim GitHub releases, `Cloudbus/cloudsim`, and place it in `cloudsim/lib/`).

```bash
cd cloudsim
run.bat        # Windows
./run.sh       # Linux / Mac
```

Or in VS Code: install the "Extension Pack for Java", add `cloudsim/lib/cloudsim-3.0.3.jar` under *Java Projects > Referenced Libraries*, open `LostFoundSimulation.java` and click **Run**.

The output prints each request's start/finish time per scenario, then a summary table (average response time, total time, estimated cost) for 1, 2 and 4 VMs.

---

## 6. Cloud concepts demonstrated (for your report / viva)

- **IaaS**: EC2 / VirtualBox VMs; **PaaS-style storage**: S3; **SaaS**: the portal itself
- **Containerisation**: Docker image + Compose, volumes for persistence
- **Scalability and elasticity**: Auto Scaling Group + ALB, validated by CloudSim
- **High availability**: multi-AZ deployment, `/health` checks
- **Security**: private S3 bucket with presigned URLs, IAM role (no hard-coded keys), contact details hidden from public API, upload type/size validation, admin authentication
- **Cost optimisation**: free-tier instance, scale in off-peak, CloudSim cost estimate

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | dev value | Flask session signing, **change in production** |
| `ADMIN_PASSWORD` | `admin123` | Admin login, **change in production** |
| `S3_BUCKET` | unset | Enables S3 photo storage |
| `AWS_REGION` | `ap-south-1` | S3 region |
| `DATA_DIR` | `app/data` | Location of SQLite DB and local uploads |
| `PORT` | `5000` | Server port |

## Troubleshooting

- *Port 5000 in use*: `set PORT=5001` (Windows) / `PORT=5001 python app.py`.
- *Docker "daemon not running"*: start Docker Desktop first.
- *CloudSim "package org.cloudbus.cloudsim does not exist"*: the jar is missing from `cloudsim/lib/`.
- *Reset demo data*: delete the `app/data` folder and restart.
