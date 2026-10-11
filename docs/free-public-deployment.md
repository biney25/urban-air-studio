# Free public demonstration

This deployment serves the real React frontend, API, native Cedar authorization and bounded T1 research-preview worker in one Render Free web service. It starts with an empty, separate store. Each browser receives its own signed session and tenant; no private local studies are uploaded. A session represents a browser, not a verified person or enterprise account.

Render Free sleeps after 15 minutes without requests; startup may take about a minute. The filesystem is temporary and all studies are lost on sleep, restart or redeployment. Download reports you want to retain. No payment card, paid instance, managed database or cloud AI is required. This is a hackathon demonstration, not production hosting.

OpenFOAM/WSL detailed solver execution and local Ollama/Strands AI execution remain available in the local installation; they are not hosted by this free container. T1 outputs stay labelled research previews, with no accuracy, AQI or environmental-benefit claim.

To deploy: create a Render Web Service from the public repository, choose Docker, branch main, Dockerfile `infra/Dockerfile.public-demo`, root directory blank, region Singapore, instance Free and health path `/api/health`. Render supplies `RENDER_EXTERNAL_URL` for the exact allowed browser origin. Build context uses the audited public source only. Never deploy from the private workspace or expose its development API through a tunnel.

Configuration is in `render.yaml`. The public wrapper is separate from the accepted local app. Run its meaningful isolation/origin/cap tests before publishing any changes. Free-host workload and storage caps deliberately stop requests rather than invent results or spend money.
