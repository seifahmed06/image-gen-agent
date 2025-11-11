# image-gen-agent
AI Agent for image generation with cost control and approval workflow
---

The **Image-Gen Agent** is designed with a modular extension layer that allows you to connect different
AI image generation backends or add custom approval and monitoring logic.

### 🔧 Supported Extensions

| Extension Type | Description | Example |
|----------------|--------------|----------|
| **MCP Server** | Core integration used for generating images. Any MCP-compatible REST endpoint can be plugged in. | `http://localhost:8080/generate_image` |
| **Approval Workflow** | External systems can approve or reject pending bulk requests via the `/approve/{job_id}` endpoint. | Slack bot, internal API, etc. |
| **Cost Tracking** | Cost per image (`COST_PER_IMAGE`) and total spend are tracked automatically via `/stats`. | Cloud billing or CSV export |
| **Notification Hooks (optional)** | You can extend the agent to send completion or rejection notifications. | Webhook, email, Slack, Discord |

### ⚙️ Configuration Variables

| Variable | Purpose | Default |
|-----------|----------|----------|
| `MCP_SERVER_URL` | URL of your image generation MCP server | `http://localhost:8080/generate_image` |
| `COST_PER_IMAGE` | Estimated cost per generated image | `0.05` |
| `APPROVER_SECRET` | Secret token for `/approve/{job_id}` endpoint | `secret-token` |
| `MAX_IMAGES_PER_REQUEST` | Limit of images per request | `10` |
| `REQUEST_TIMEOUT` | Timeout for MCP server requests (seconds) | `60.0` |

### 🧠 Adding a New Extension

To add your own extension (for example, another image model or billing backend):

1. Implement a new async function in `agent.py` that calls your API (see `call_mcp_generate()` as a reference).
2. Add any new environment variables to `.env.example`.
3. Update `/stats` or `/generate` logic to include your feature.
4. Rebuild Docker image:
   ```bash
   docker build -t image-gen-agent .
