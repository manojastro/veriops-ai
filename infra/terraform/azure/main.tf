# Reference production architecture on Azure. Optional: local development needs none of this.
#
#   AKS (workload identity, Key Vault CSI) ── ACR
#     │   ├─ api / worker ──► Azure OpenAI (gpt-4.1, gpt-4.1-mini, text-embedding-3-small)
#     │   ├─ api / worker ──► PostgreSQL Flexible Server (pgvector) · Azure Cache for Redis
#     │   └─ OTel collector ─► Application Insights / Log Analytics (Azure Monitor)
#     └── Key Vault (DB URL, Redis URL, JWT secret, OpenAI endpoint) · Blob Storage (reports)
#   Entra ID: workload identity federation for pods; Entra groups map to VeriOps roles (future SSO).

data "azurerm_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 5
  upper   = false
  special = false
}

locals {
  name = "${var.prefix}${random_string.suffix.result}"
}

resource "azurerm_resource_group" "main" {
  name     = "rg-${var.prefix}"
  location = var.location
  tags     = var.tags
}

# --- observability -------------------------------------------------------------------------
resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${local.name}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = var.tags
}

resource "azurerm_application_insights" "main" {
  name                = "appi-${local.name}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  workspace_id        = azurerm_log_analytics_workspace.main.id
  application_type    = "web"
  tags                = var.tags
}

# --- registry & cluster ----------------------------------------------------------------------
resource "azurerm_container_registry" "main" {
  name                = "acr${local.name}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "Standard"
  admin_enabled       = false
  tags                = var.tags
}

resource "azurerm_kubernetes_cluster" "main" {
  name                      = "aks-${local.name}"
  location                  = azurerm_resource_group.main.location
  resource_group_name       = azurerm_resource_group.main.name
  dns_prefix                = local.name
  oidc_issuer_enabled       = true
  workload_identity_enabled = true
  local_account_disabled    = true
  sku_tier                  = "Standard"

  default_node_pool {
    name                 = "system"
    vm_size              = var.aks_node_vm_size
    auto_scaling_enabled = true
    min_count            = 2
    max_count            = 5
    zones                = ["1", "2", "3"]
  }

  identity {
    type = "SystemAssigned"
  }

  azure_active_directory_role_based_access_control {
    azure_rbac_enabled = true
    tenant_id          = data.azurerm_client_config.current.tenant_id
  }

  key_vault_secrets_provider {
    secret_rotation_enabled = true
  }

  oms_agent {
    log_analytics_workspace_id = azurerm_log_analytics_workspace.main.id
  }

  network_profile {
    network_plugin = "azure"
    network_policy = "azure"
  }

  tags = var.tags
}

resource "azurerm_role_assignment" "aks_acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_kubernetes_cluster.main.kubelet_identity[0].object_id
}

# Workload identity used by the api/worker pods.
resource "azurerm_user_assigned_identity" "workload" {
  name                = "id-${local.name}-workload"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = var.tags
}

resource "azurerm_federated_identity_credential" "workload" {
  name                = "veriops-serviceaccount"
  resource_group_name = azurerm_resource_group.main.name
  parent_id           = azurerm_user_assigned_identity.workload.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = azurerm_kubernetes_cluster.main.oidc_issuer_url
  subject             = "system:serviceaccount:veriops:veriops"
}

# --- data ------------------------------------------------------------------------------------
resource "random_password" "postgres" {
  length  = 32
  special = false
}

resource "azurerm_postgresql_flexible_server" "main" {
  name                          = "psql-${local.name}"
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  version                       = "16"
  sku_name                      = var.postgres_sku
  storage_mb                    = 65536
  backup_retention_days         = 14
  geo_redundant_backup_enabled  = true
  administrator_login           = "veriopsadmin"
  administrator_password        = random_password.postgres.result
  public_network_access_enabled = false
  zone                          = "1"
  tags                          = var.tags
}

resource "azurerm_postgresql_flexible_server_configuration" "extensions" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "VECTOR"
}

resource "azurerm_postgresql_flexible_server_database" "veriops" {
  name      = "veriops"
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

resource "azurerm_redis_cache" "main" {
  name                          = "redis-${local.name}"
  location                      = azurerm_resource_group.main.location
  resource_group_name           = azurerm_resource_group.main.name
  capacity                      = 1
  family                        = "C"
  sku_name                      = "Standard"
  minimum_tls_version           = "1.2"
  non_ssl_port_enabled          = false
  public_network_access_enabled = false
  tags                          = var.tags
}

resource "azurerm_storage_account" "reports" {
  name                            = "st${local.name}"
  location                        = azurerm_resource_group.main.location
  resource_group_name             = azurerm_resource_group.main.name
  account_tier                    = "Standard"
  account_replication_type        = "ZRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  tags                            = var.tags
}

resource "azurerm_storage_container" "reports" {
  name                  = "postmortems"
  storage_account_id    = azurerm_storage_account.reports.id
  container_access_type = "private"
}

# --- Azure OpenAI (Microsoft Foundry) ----------------------------------------------------------
resource "azurerm_cognitive_account" "openai" {
  name                  = "oai-${local.name}"
  location              = var.openai_location
  resource_group_name   = azurerm_resource_group.main.name
  kind                  = "OpenAI"
  sku_name              = "S0"
  custom_subdomain_name = "oai-${local.name}"
  local_auth_enabled    = true
  tags                  = var.tags
}

resource "azurerm_cognitive_deployment" "reasoning" {
  name                 = "gpt-4.1"
  cognitive_account_id = azurerm_cognitive_account.openai.id
  model {
    format  = "OpenAI"
    name    = "gpt-4.1"
    version = "2025-04-14"
  }
  sku {
    name     = "GlobalStandard"
    capacity = 50
  }
}

resource "azurerm_cognitive_deployment" "small" {
  name                 = "gpt-4.1-mini"
  cognitive_account_id = azurerm_cognitive_account.openai.id
  model {
    format  = "OpenAI"
    name    = "gpt-4.1-mini"
    version = "2025-04-14"
  }
  sku {
    name     = "GlobalStandard"
    capacity = 100
  }
}

resource "azurerm_cognitive_deployment" "embeddings" {
  name                 = "text-embedding-3-small"
  cognitive_account_id = azurerm_cognitive_account.openai.id
  model {
    format  = "OpenAI"
    name    = "text-embedding-3-small"
    version = "1"
  }
  sku {
    name     = "Standard"
    capacity = 100
  }
}

resource "azurerm_role_assignment" "workload_openai" {
  scope                = azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.workload.principal_id
}

# --- secrets ---------------------------------------------------------------------------------
resource "azurerm_key_vault" "main" {
  name                       = "kv-${local.name}"
  location                   = azurerm_resource_group.main.location
  resource_group_name        = azurerm_resource_group.main.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  purge_protection_enabled   = true
  soft_delete_retention_days = 30
  tags                       = var.tags
}

resource "azurerm_role_assignment" "workload_kv" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.workload.principal_id
}

resource "azurerm_role_assignment" "deployer_kv" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "random_password" "jwt" {
  length  = 64
  special = false
}

resource "azurerm_key_vault_secret" "secrets" {
  for_each = {
    "database-url"            = "postgresql+psycopg://veriopsadmin:${random_password.postgres.result}@${azurerm_postgresql_flexible_server.main.fqdn}:5432/veriops?sslmode=require"
    "redis-url"               = "rediss://:${azurerm_redis_cache.main.primary_access_key}@${azurerm_redis_cache.main.hostname}:6380/0"
    "jwt-secret"              = random_password.jwt.result
    "azure-openai-endpoint"   = azurerm_cognitive_account.openai.endpoint
    "azure-openai-api-key"    = azurerm_cognitive_account.openai.primary_access_key
    "azure-openai-deployment" = azurerm_cognitive_deployment.reasoning.name
  }
  name         = each.key
  value        = each.value
  key_vault_id = azurerm_key_vault.main.id
  depends_on   = [azurerm_role_assignment.deployer_kv]
}
