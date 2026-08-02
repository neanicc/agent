"use client";

import { useMemo, useState } from "react";

import { controlClient } from "@/lib/api/use-control-query";
import type { components } from "@/lib/api/generated";

export type PolicySeverity = components["schemas"]["PreferenceRuleView"]["severity"];
export type PolicyRule = components["schemas"]["PreferenceRuleView"];
export type PolicyProfile = components["schemas"]["PreferenceProfileView"];

type PolicyEditorProps = {
  profile: PolicyProfile;
  saveProfile?: (rules: PolicyRule[]) => Promise<PolicyProfile>;
};

const severityRank: Record<PolicySeverity, number> = {
  inform: 0,
  warn: 1,
  block: 2,
};

export function PolicyEditor({ profile, saveProfile = saveThroughControlApi }: PolicyEditorProps) {
  const [rules, setRules] = useState(() => profile.rules.map((rule) => ({ ...rule })));
  const [baselineRules, setBaselineRules] = useState(() => profile.rules.map((rule) => ({ ...rule })));
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const managedMinimums = useMemo(
    () => new Map(profile.rules.filter((rule) => rule.managed).map((rule) => [rule.id, rule.severity])),
    [profile.rules],
  );
  const weakenedRule = rules.find((rule) => {
    const minimum = managedMinimums.get(rule.id);
    return minimum !== undefined && severityRank[rule.severity] < severityRank[minimum];
  });
  const dirty = JSON.stringify(rules) !== JSON.stringify(baselineRules);

  const updateSeverity = (id: string, severity: PolicySeverity) => {
    setSaveState("idle");
    setRules((current) => current.map((rule) => (rule.id === id ? { ...rule, severity } : rule)));
  };

  const save = async () => {
    if (weakenedRule || !dirty || saveState === "saving") return;
    setSaveState("saving");
    try {
      const saved = await saveProfile(rules);
      setRules(saved.rules.map((rule) => ({ ...rule })));
      setBaselineRules(saved.rules.map((rule) => ({ ...rule })));
      setSaveState("saved");
    } catch {
      setSaveState("error");
    }
  };

  return (
    <section aria-labelledby="policy-rules-heading" className="policy-editor">
      <div className="section-heading">
        <div>
          <h2 id="policy-rules-heading">Effective rules</h2>
          <p>Managed minimums take precedence over repository and personal preferences.</p>
        </div>
        <span className="telemetry">v{profile.profile_version}</span>
      </div>
      <p className="policy-manifest">
        Source manifest <code>{profile.source_manifest_hash}</code>
      </p>
      <div className="policy-rule-list">
        {rules.map((rule) => (
          <fieldset className="policy-rule" key={rule.id}>
            <legend>{rule.id}</legend>
            <div className="policy-rule__control">
              <label htmlFor={`severity-${rule.id}`}>Severity</label>
              <select
                aria-label={`${rule.id} severity`}
                id={`severity-${rule.id}`}
                onChange={(event) => updateSeverity(rule.id, event.target.value as PolicySeverity)}
                value={rule.severity}
              >
                <option value="inform">Inform</option>
                <option value="warn">Warn</option>
                <option value="block">Block</option>
              </select>
            </div>
            <dl className="policy-rule__facts">
              <div>
                <dt>Source</dt>
                <dd>{title(rule.source ?? (rule.managed ? "organization" : "profile"))}</dd>
              </div>
              <div>
                <dt>Precedence</dt>
                <dd>{title(rule.precedence ?? (rule.managed ? "managed minimum" : "profile rule"))}</dd>
              </div>
              <div>
                <dt>Affects</dt>
                <dd>{titleList(rule.affected_capabilities)}</dd>
              </div>
            </dl>
          </fieldset>
        ))}
      </div>
      {weakenedRule ? (
        <p className="form-error" role="alert">
          Managed safety rules cannot be weakened
        </p>
      ) : null}
      <div className="policy-editor__controls">
        <button
          className="button"
          disabled={Boolean(weakenedRule) || !dirty || saveState === "saving"}
          onClick={() => void save()}
          type="button"
        >
          {saveState === "saving" ? "Saving policy" : "Save policy"}
        </button>
        <span aria-live="polite" className="form-status">
          {saveState === "saved"
            ? "Policy saved. Capabilities will use the new profile version."
            : saveState === "error"
              ? "Policy could not be saved. Server policy remains unchanged."
              : ""}
        </span>
      </div>
    </section>
  );
}

async function saveThroughControlApi(rules: PolicyRule[]): Promise<PolicyProfile> {
  return controlClient().request<PolicyProfile>("/v1/preferences", {
    method: "PUT",
    body: JSON.stringify({
      rules: rules.map(({ id, severity }) => ({ id, severity })),
    }),
  });
}

function title(value: string): string {
  return value
    .split(/[_-]/)
    .map((part) => `${part.slice(0, 1).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

function titleList(values: string[] | undefined): string {
  if (!values?.length) return "Capability impact not reported";
  return values.map((value, index) => (index === 0 ? title(value) : value.replaceAll("_", " "))).join(", ");
}
