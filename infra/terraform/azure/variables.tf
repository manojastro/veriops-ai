variable "subscription_id" {
  type        = string
  description = "Azure subscription id"
}

variable "prefix" {
  type        = string
  default     = "veriops"
  description = "Resource name prefix (lowercase, alphanumeric)"
}

variable "location" {
  type    = string
  default = "westeurope"
}

variable "openai_location" {
  type        = string
  default     = "swedencentral"
  description = "Region with Azure OpenAI model availability"
}

variable "aks_node_vm_size" {
  type    = string
  default = "Standard_D4s_v5"
}

variable "postgres_sku" {
  type    = string
  default = "GP_Standard_D2ds_v5"
}

variable "tags" {
  type = map(string)
  default = {
    project = "veriops-ai"
    owner   = "platform-sre"
  }
}
