# Smart Energy Monitor - deploy the Azure Data Factory pipeline.
# Run these lines ONE BLOCK AT A TIME in PowerShell, from the "DR HAGAG" folder.
# Needs the Azure CLI (az). Nothing here prints a password or key.

# ---- 0. Fill in your names ONCE (from portal.azure.com -> your Synapse workspace -> Overview) ----
$RG   = "PUT-RESOURCE-GROUP-HERE"      # Resource group
$WS   = "PUT-SYNAPSE-WORKSPACE-HERE"   # Synapse workspace name
$POOL = "PUT-SPARK-POOL-HERE"          # Analytics pools -> Apache Spark pools
$ADF  = "adf-smart-energy-habiba"      # new Data Factory name (must be unique in Azure; change if taken)
$ST   = "powerconsumption2026"         # storage account (from the Spark document)

# ---- 1. Sign in ----
az login

# ---- 2. Create the Data Factory + pipeline + trigger (trigger is created STOPPED) ----
az deployment group create --resource-group $RG --template-file adf\adf_template.json `
  --parameters factoryName=$ADF synapseWorkspace=$WS sparkPool=$POOL storageAccount=$ST

# ---- 3. Permissions ----
# the Data Factory's own identity
$ADF_MI = az resource show -g $RG -n $ADF --resource-type Microsoft.DataFactory/factories --query identity.principalId -o tsv
$ST_ID  = az storage account show -n $ST --query id -o tsv

# 3a. Data Factory may READ the data lake (for the "check" steps)
az role assignment create --assignee-object-id $ADF_MI --assignee-principal-type ServicePrincipal `
  --role "Storage Blob Data Reader" --scope $ST_ID

# 3b. Data Factory may RUN notebooks in Synapse
az synapse role assignment create --workspace-name $WS --role "Synapse Administrator" `
  --assignee-object-id $ADF_MI --assignee-principal-type ServicePrincipal

# 3c. Synapse itself may READ/WRITE the data lake when a pipeline runs the notebooks
#     (when you ran them by hand they used YOUR account; a pipeline uses the workspace identity)
$WS_MI = az synapse workspace show -n $WS -g $RG --query identity.principalId -o tsv
az role assignment create --assignee-object-id $WS_MI --assignee-principal-type ServicePrincipal `
  --role "Storage Blob Data Contributor" --scope $ST_ID

# ---- 4. Wait 2-5 minutes (permissions take time), then run it ONCE by hand ----
# portal.azure.com -> Data factories -> adf-smart-energy-habiba -> Launch studio
# -> Author -> Pipelines -> PL_SmartEnergy_Weekly -> Add trigger -> Trigger now
# -> Monitor -> Pipeline runs  (take screenshots here for the report)

# ---- 5. OPTIONAL: start the weekly trigger (every run uses Azure credit) ----
# az extension add --name datafactory
# az datafactory trigger start --resource-group $RG --factory-name $ADF --name TR_Weekly
# to stop it later:
# az datafactory trigger stop --resource-group $RG --factory-name $ADF --name TR_Weekly
