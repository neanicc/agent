{{- define "loopguard.name" -}}loopguard{{- end -}}
{{- define "loopguard.labels" -}}
app.kubernetes.io/name: {{ include "loopguard.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}
{{- define "loopguard.selectorLabels" -}}
app.kubernetes.io/name: {{ include "loopguard.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
{{- define "loopguard.image" -}}
{{- $repository := index . 0 -}}
{{- $digest := index . 1 -}}
{{- if not (regexMatch "^sha256:[a-f0-9]{64}$" $digest) -}}
{{- fail "all LoopGuard images must use a sha256 digest" -}}
{{- end -}}
{{- printf "%s@%s" $repository $digest -}}
{{- end -}}
{{- define "loopguard.podSecurityContext" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- end -}}
{{- define "loopguard.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end -}}
{{- define "loopguard.topologySpread" -}}
- maxSkew: 1
  topologyKey: topology.kubernetes.io/zone
  whenUnsatisfiable: DoNotSchedule
  labelSelector:
    matchLabels:
      {{- include "loopguard.selectorLabels" . | nindent 6 }}
{{- end -}}
