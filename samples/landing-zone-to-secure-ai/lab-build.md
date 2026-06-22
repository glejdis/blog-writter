# Lab Build: Enterprise-Ready Azure AI Foundation for Agents

> Companion build guide for the blog **"From Azure Landing Zone to Secure AI: A
> Practical Guide to Enterprise Agentic Deployment (Part 1)."**
> This lab makes the story reproducible. You build the **platform foundation
> (Maya)** first, then the **agent (Sam)** on top of it.
> The single handoff between the two halves is the same as in the blog: a
> **managed identity** + **private endpoints** + **firewall egress rules**.

---

## How to start (read this first)

The biggest mistake when reproducing this architecture is starting with the
Azure OpenAI resource. Don't. Build in this order:

1. **Foundation & scope** — subscription, region, naming, tags.
2. **Networking** — hub (or simulated hub), AI spoke VNet, subnets (including a
   **delegated agent subnet**), Private DNS zones, **Azure Firewall** for egress.
3. **Identity** — user-assigned managed identity, *before* any AI service exists.
4. **Agent state resources** — Cosmos DB, Storage, AI Search, Key Vault (the BYO
   resources the Foundry **standard agent setup** requires), private only.
5. **AI services, private only** — Foundry / Azure OpenAI with public access
   disabled and private endpoints.
6. **Role assignments** — least-privilege RBAC binding the identity to each
   service.
7. **Guardrails & governance** — Content Safety / Prompt Shields, budget alerts,
   tags, diagnostics.
8. **Only now: the agent** — tools, model, evaluation.

If you only have a few hours, do **Phase 0–3 + the egress firewall** end to end.
That is the part the blog argues is most often skipped, and it is the part that
actually protects you.

> **Recommended first move:** start with **Option A (single-subscription lab)**.
> It reproduces every security control from the blog without needing Management
> Group rights or an existing landing zone.

---

## Choose your path

| Option | Use when | What you simulate |
| --- | --- | --- |
| **A. Single-subscription lab** *(recommended start)* | One subscription, Contributor + User Access Administrator | Hub + spoke as two peered VNets in one sub |
| **B. Real landing zone spoke** | You already operate an ALZ with a hub | A genuine dedicated AI subscription peered to the real hub |

The guide is written for **Option A** and calls out where **Option B** differs.

---

## Prerequisites

- An Azure subscription with **Contributor** + **User Access Administrator**.
- Azure CLI `az` (>= 2.50) and Bicep, **or** Terraform. Examples use Azure CLI.
- A region that offers your target models **and** Foundry Agent Service standard
  setup (for example `swedencentral`, `eastus2`). Verify before committing.
- A jumpbox / VM **inside the spoke VNet**, or VPN/Bastion — once public access is
  disabled you can only reach the services from inside the network.
- Resource providers registered (Agent Service standard setup needs them):
  `Microsoft.CognitiveServices`, `Microsoft.Storage`, `Microsoft.Search`,
  `Microsoft.DocumentDB` (Cosmos DB), `Microsoft.KeyVault`, `Microsoft.Network`,
  `Microsoft.App`, `Microsoft.MachineLearningServices`.

> **Verify before you build:** Azure AI product names, model + region
> availability, private DNS zone namespaces, and **Foundry RBAC role names**
> (recently renamed) change frequently. Confirm in Microsoft Learn / the Foundry
> portal before deploying.

---

## Naming & tagging convention

```
rg-ai-<env>               # resource group
vnet-hub-<env>            # simulated hub
vnet-ai-spoke-<env>       # AI spoke
snet-pep-<env>            # private endpoints subnet
snet-agent-<env>          # delegated subnet for Agent Service compute
id-ai-app-<env>           # user-assigned managed identity (one per agent)
oai-ai-<env>              # Azure OpenAI / Foundry account
srch-ai-<env>             # Azure AI Search
stai<env><uniq>           # Storage (no dashes, <=24 chars, lowercase)
cosmos-ai-<env>           # Cosmos DB (agent thread + metadata store)
kv-ai-<env>               # Key Vault
afw-ai-<env>              # Azure Firewall (egress control)
```

Tags applied to every resource:

```
workload      = ai-agent
owner         = platform-team
costCenter    = <your-cc>
dataClass     = internal
environment   = lab
```

---

# Part A — Build the platform foundation (Maya)

## Phase 0 — Subscription, resource group, region

```bash
az login
az account set --subscription "<sub-id>"

LOC=swedencentral
ENV=lab
RG=rg-ai-$ENV

az group create -n $RG -l $LOC \
  --tags workload=ai-agent owner=platform-team environment=$ENV dataClass=internal
```

**Option B note:** create/identify the **dedicated AI subscription** and deploy
the resource group there; skip the simulated hub in Phase 1.

---

## Phase 1 — Networking (hub, spoke, subnets, DNS, egress firewall)

Goal: a spoke VNet with a private-endpoint subnet **and** a delegated subnet for
agent compute, peered to a hub, with Private DNS zones and an **Azure Firewall**
that controls all outbound traffic.

### 1.1 VNets and subnets

```bash
# Simulated hub (Option A only)
az network vnet create -g $RG -n vnet-hub-$ENV --address-prefix 10.0.0.0/16 \
  --subnet-name AzureFirewallSubnet --subnet-prefix 10.0.1.0/26

# AI spoke
az network vnet create -g $RG -n vnet-ai-spoke-$ENV --address-prefix 10.1.0.0/16 \
  --subnet-name snet-app --subnet-prefix 10.1.1.0/24

# Private endpoints subnet
az network vnet subnet create -g $RG --vnet-name vnet-ai-spoke-$ENV \
  -n snet-pep --address-prefix 10.1.2.0/24

# Delegated subnet for Foundry Agent Service compute (BYO VNet injection)
az network vnet subnet create -g $RG --vnet-name vnet-ai-spoke-$ENV \
  -n snet-agent --address-prefix 10.1.3.0/24
```

> The agent subnet must be delegated to Agent Service per the current standard
> setup docs. Confirm the exact delegation name at build time.

### 1.2 Peer hub and spoke (Option A)

```bash
az network vnet peering create -g $RG -n hub-to-spoke \
  --vnet-name vnet-hub-$ENV --remote-vnet vnet-ai-spoke-$ENV \
  --allow-vnet-access --allow-forwarded-traffic

az network vnet peering create -g $RG -n spoke-to-hub \
  --vnet-name vnet-ai-spoke-$ENV --remote-vnet vnet-hub-$ENV \
  --allow-vnet-access --allow-forwarded-traffic
```

**Option B note:** peer the spoke to your **existing hub**; DNS zones and the
firewall likely already exist there — link/reuse rather than create.

### 1.3 Private DNS zones

Create one zone per service and link it to the spoke:

```bash
for ZONE in \
  privatelink.openai.azure.com \
  privatelink.cognitiveservices.azure.com \
  privatelink.search.windows.net \
  privatelink.blob.core.windows.net \
  privatelink.documents.azure.com \
  privatelink.vaultcore.azure.net ; do
    az network private-dns zone create -g $RG -n $ZONE
    az network private-dns link vnet create -g $RG --zone-name $ZONE \
      -n link-spoke --virtual-network vnet-ai-spoke-$ENV --registration-enabled false
done
```

> These are the current privatelink namespaces. Foundry / AI Services may require
> the `cognitiveservices` and/or `services.ai.azure.com` zones in addition to
> `openai.azure.com` — confirm at build time, they evolve as the platform does.

### 1.4 Azure Firewall — control egress (the agentic control)

An agent's tool calls are *outbound*. Force them through the firewall and
allow-list only approved destinations.

```bash
az network firewall create -g $RG -n afw-ai-$ENV -l $LOC
# Create a public IP + IP config, then a route table on snet-agent / snet-app
# whose 0.0.0.0/0 next hop is the firewall private IP.
# Add an application rule collection allowing only the FQDNs your agent needs,
# e.g. your approved API endpoints. Deny everything else by default.
```

> This is the single control that separates "an agent that can reach the one API
> it needs" from "an agent that can be talked into exfiltrating data anywhere."

---

## Phase 2 — Identity BEFORE infrastructure

This is the heart of the blog's argument. Create the identity now, even though no
AI service exists yet. Use **one identity per agent**, not a shared fleet
identity.

```bash
az identity create -g $RG -n id-ai-app-$ENV -l $LOC
APP_ID_PRINCIPAL=$(az identity show -g $RG -n id-ai-app-$ENV --query principalId -o tsv)
echo "Managed identity principalId: $APP_ID_PRINCIPAL"
```

Keep `$APP_ID_PRINCIPAL` — it is **artifact #1** of the Part A -> Part B handoff.

---

## Phase 3 — Agent state resources (BYO), private only

Foundry **standard agent setup** keeps agent data in *your* tenant. Provision the
BYO resources up front, each with **public network access disabled** and a
**private endpoint** into `snet-pep`:

| Resource | Purpose | `--group-id` | Private DNS zone |
| --- | --- | --- | --- |
| Azure Cosmos DB (NoSQL) | Threads/conversations + agent metadata (>=3000 RU/s) | `Sql` | `privatelink.documents.azure.com` |
| Azure Storage (blob) | Uploaded files + intermediate data | `blob` | `privatelink.blob.core.windows.net` |
| Azure AI Search | Vector store / index | `searchService` | `privatelink.search.windows.net` |
| Azure Key Vault | Secrets for agent infra | `vault` | `privatelink.vaultcore.azure.net` |

For each: disable public network access, create the private endpoint into
`snet-pep`, and add a DNS zone group. (Cosmos DB needs a total throughput of at
least 3000 RU/s — standard setup provisions three 1000 RU/s containers.)

---

## Phase 4 — AI services, private only

Deploy the Foundry / Azure OpenAI account with **public network access disabled**
and a **private endpoint** into `snet-pep`.

```bash
az cognitiveservices account create -g $RG -n oai-ai-$ENV -l $LOC \
  --kind OpenAI --sku S0 \
  --custom-domain oai-ai-$ENV \
  --public-network-access Disabled \
  --assign-identity

OAI_ID=$(az cognitiveservices account show -g $RG -n oai-ai-$ENV --query id -o tsv)

az network private-endpoint create -g $RG -n pep-oai-$ENV \
  --vnet-name vnet-ai-spoke-$ENV --subnet snet-pep \
  --private-connection-resource-id $OAI_ID \
  --group-id account --connection-name oai-conn

az network private-endpoint dns-zone-group create -g $RG \
  --endpoint-name pep-oai-$ENV -n zg-oai \
  --private-dns-zone privatelink.openai.azure.com --zone-name openai
```

> For a full Foundry Agent Service standard setup, the recommended path is the
> Bicep/Terraform quickstart in the Agent Service docs, which wires the account,
> project, capability hosts, model deployment, and the BYO connections together.
> The CLI above shows the security posture; IaC is what you'd actually commit.

---

## Phase 5 — Least-privilege role assignments

Bind the managed identity to each service with the minimum role. A **read-only**
agent gets exactly these — no key access, no write:

```bash
# Azure OpenAI — inference calls via Entra ID only
az role assignment create --assignee-object-id $APP_ID_PRINCIPAL \
  --assignee-principal-type ServicePrincipal \
  --role "Cognitive Services OpenAI User" --scope $OAI_ID

# AI Search — query the index
az role assignment create --assignee-object-id $APP_ID_PRINCIPAL \
  --assignee-principal-type ServicePrincipal \
  --role "Search Index Data Reader" --scope <search-id>

# Storage — read source documents
az role assignment create --assignee-object-id $APP_ID_PRINCIPAL \
  --assignee-principal-type ServicePrincipal \
  --role "Storage Blob Data Reader" --scope <storage-id>
```

No keys. No connection strings. The day the agent needs to **write** (file a
ticket, update a record), add a *separate, explicitly scoped* role for that one
action — never a broad Contributor.

---

## Phase 6 — Guardrails & governance

```bash
# Budget alert (example: $200/mo, alert at 80%)
az consumption budget create --budget-name ai-lab-budget \
  --amount 200 --category Cost --time-grain Monthly \
  --start-date 2026-06-01 --end-date 2026-12-31 \
  --resource-group $RG
```

Also enable:

- **Azure AI Content Safety / Prompt Shields** on the prompt + document path, so
  poisoned content is screened before the agent acts on it.
- **Diagnostic settings -> Log Analytics** on every AI service (you cannot debug
  or defend a non-deterministic system you cannot observe).
- **Azure Policy** to *deny* public network access on Cognitive Services, Search,
  Storage, and Cosmos DB.
- Confirm tags are present on every resource.

---

## Part A outcome — the handoff

Nothing intelligent is deployed yet, but you can hand Sam:

1. **`$APP_ID_PRINCIPAL`** — a managed identity with least-privilege roles.
2. **Private endpoints** — every AI + state service reachable only inside the spoke.
3. **A delegated agent subnet + firewall egress rules** — the agent can only reach
   approved destinations.
4. **Prompt Shields, budgets, diagnostics** — on by default.

Validate from the jumpbox inside the spoke:

```bash
nslookup oai-ai-lab.openai.azure.com   # should resolve to a 10.1.2.x private IP
```

If it resolves to a public IP, your DNS zone link or zone group is wrong — fix
before proceeding.

---

# Part B — Build the agent (Sam)

> Everything below runs **from inside the network**, authenticates **via the
> managed identity**, and reaches services **over private endpoints**. Outbound
> tool calls go **through the firewall**. No step reintroduces a public path or a
> secret.

## Phase 7 — Documents -> a tool (ingestion)

1. Upload source documents to the private **Storage** account (from the jumpbox).
2. Build an ingestion job that **chunks** documents, generates **embeddings**, and
   writes vectors to **AI Search** — authenticated by `id-ai-app-lab`, no keys.
3. In the agent, this becomes the **AI Search / file-search tool**, not the whole
   app.

## Phase 8 — Define the agent

- Choose **prompt agent** (declarative, hosted by Agent Service) for read-only Q&A.
- Choose a **hosted / code-driven agent** (e.g. Microsoft Agent Framework) when you
  need deterministic orchestration, multi-agent calls, or **human-in-the-loop**
  approval before consequential actions.
- Configure connected tools and confirm each tool's egress FQDN is allow-listed on
  the firewall.

## Phase 9 — Model selection

Now the opening question finally matters. Compare candidate models on accuracy,
latency, cost, context window, and data sensitivity. Deploy the chosen model into
the existing **private** Foundry / Azure OpenAI resource.

## Phase 10 — Evaluation before production

Evaluate response quality, grounding accuracy, hallucination rate,
**prompt-injection resistance**, safety behavior, latency, and cost. Ship only
when you have confidence, not just a working demo.

## Phase 11 — Bring the two worlds together

The agent calls its model and tools **through the managed identity, over the
private endpoints, with egress constrained by the firewall rules from Phase 1**.
That line is the payoff of the whole build.

---

## Teardown

```bash
az group delete -n $RG --yes --no-wait
```

In **Option B**, delete only the resources you created in the spoke — never the
shared hub, firewall, or landing zone components.

---

## Suggested repo layout (if you turn this into IaC)

```
/infra
  /modules
    network.bicep        # vnets, subnets (incl. delegated agent subnet), peering, DNS
    firewall.bicep       # Azure Firewall + egress rules + route tables
    identity.bicep       # user-assigned managed identity (one per agent)
    state.bicep          # cosmos + storage + search + key vault (BYO agent state)
    ai-service.bicep     # foundry/openai account + PE + DNS zone group
    rbac.bicep           # least-privilege role assignments
    governance.bicep     # budget, policy, diagnostics, content safety
  main.bicep             # composes Part A
/agent
  ingestion/             # chunk + embed + index
  agent/                 # prompt/hosted agent definition + tools
  eval/                  # evaluation datasets + scoring
```

---

## Mapping to the blog

| Blog section | Lab phase |
| --- | --- |
| Where will this workload live? | Phase 0 + Choose Your Path |
| Zero Trust: private connectivity + controlled egress | Phases 1, 3, 4 |
| Identity before infrastructure | Phase 2 |
| Untrusted content is a boundary too | Phase 6 (Prompt Shields) |
| Governance, cost, observability | Phase 6 |
| The handoff contract | Part A outcome |
| Documents become a tool | Phase 7 |
| Designing the agent (prompt vs hosted) | Phase 8 |
| Now the model question matters | Phase 9 |
| Evaluate before production | Phase 10 |
| Bringing the two worlds together | Phase 11 |
