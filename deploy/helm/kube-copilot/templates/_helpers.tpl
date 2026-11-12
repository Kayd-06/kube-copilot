{{/* vim: set filetype=mustache: */}}
{{- define "kube-copilot.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "kube-copilot.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "kube-copilot.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "kube-copilot.labels" -}}
helm.sh/chart: {{ include "kube-copilot.chart" . }}
{{ include "kube-copilot.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: kube-copilot
{{- end }}

{{- define "kube-copilot.selectorLabels" -}}
app.kubernetes.io/name: {{ include "kube-copilot.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "kube-copilot.backend.selectorLabels" -}}
{{ include "kube-copilot.selectorLabels" . }}
app.kubernetes.io/component: backend
{{- end }}

{{- define "kube-copilot.frontend.selectorLabels" -}}
{{ include "kube-copilot.selectorLabels" . }}
app.kubernetes.io/component: frontend
{{- end }}

{{- define "kube-copilot.qdrant.selectorLabels" -}}
{{ include "kube-copilot.selectorLabels" . }}
app.kubernetes.io/component: qdrant
{{- end }}

{{- define "kube-copilot.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "kube-copilot.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "kube-copilot.backend.image" -}}
{{- $tag := .Values.backend.image.tag | default .Chart.AppVersion -}}
{{- printf "%s:%s" .Values.backend.image.repository $tag -}}
{{- end }}

{{- define "kube-copilot.frontend.image" -}}
{{- $tag := .Values.frontend.image.tag | default .Chart.AppVersion -}}
{{- printf "%s:%s" .Values.frontend.image.repository $tag -}}
{{- end }}

{{- define "kube-copilot.backend.fullname" -}}
{{- printf "%s-backend" (include "kube-copilot.fullname" .) -}}
{{- end }}

{{- define "kube-copilot.frontend.fullname" -}}
{{- printf "%s-frontend" (include "kube-copilot.fullname" .) -}}
{{- end }}

{{- define "kube-copilot.qdrant.fullname" -}}
{{- printf "%s-qdrant" (include "kube-copilot.fullname" .) -}}
{{- end }}

{{- define "kube-copilot.llmSecretName" -}}
{{- if .Values.llm.existingSecret }}
{{- .Values.llm.existingSecret }}
{{- else }}
{{- printf "%s-llm" (include "kube-copilot.fullname" .) }}
{{- end }}
{{- end }}

{{- define "kube-copilot.backendUrl" -}}
{{- if .Values.frontend.backendUrl }}
{{- .Values.frontend.backendUrl }}
{{- else }}
{{- printf "http://%s:%s" (include "kube-copilot.backend.fullname" .) (.Values.backend.service.port | toString) }}
{{- end }}
{{- end }}
