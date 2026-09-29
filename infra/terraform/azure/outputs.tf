output "acr_login_server" {
  value = azurerm_container_registry.main.login_server
}

output "aks_name" {
  value = azurerm_kubernetes_cluster.main.name
}

output "key_vault_name" {
  value = azurerm_key_vault.main.name
}

output "workload_identity_client_id" {
  value = azurerm_user_assigned_identity.workload.client_id
}

output "openai_endpoint" {
  value = azurerm_cognitive_account.openai.endpoint
}

output "application_insights_connection_string" {
  value     = azurerm_application_insights.main.connection_string
  sensitive = true
}
