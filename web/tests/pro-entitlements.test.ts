import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "vitest";

import { proPlanPresentation } from "../src/lib/pro-entitlements.ts";

const typesSource = readFileSync(new URL("../src/lib/types.ts", import.meta.url), "utf8");
const activationSource = readFileSync(
  new URL("../src/components/pro-activation.tsx", import.meta.url),
  "utf8"
);
const productSource = readFileSync(
  new URL("../src/components/pro/product-spine.tsx", import.meta.url),
  "utf8"
);
const readmeSource = readFileSync(new URL("../../README.md", import.meta.url), "utf8");
const siteSource = readFileSync(new URL("../../site/index.html", import.meta.url), "utf8");
const deliverySource = readFileSync(new URL("../../docs/PRO-DELIVERY.md", import.meta.url), "utf8");

test("known entitlement identifiers have honest user-facing labels", () => {
  assert.deepEqual(proPlanPresentation("pro"), {
    kind: "local",
    label: "Local Pro",
  });
  assert.deepEqual(proPlanPresentation("pro_local"), {
    kind: "local",
    label: "Local Pro",
  });
  assert.deepEqual(proPlanPresentation("pro_cloud"), {
    kind: "cloud",
    label: "Pro Cloud",
  });
  assert.deepEqual(proPlanPresentation("dev"), {
    kind: "development",
    label: "Development override",
  });
  assert.deepEqual(proPlanPresentation("unexpected_future_plan"), {
    kind: "unknown",
    label: "Pro subscription",
  });
});

test("ProStatus types the legacy and new entitlement identifiers", () => {
  assert.match(typesSource, /export type ProPlan\s*=\s*"pro"\s*\|\s*"pro_local"\s*\|\s*"pro_cloud"\s*\|\s*"dev"/);
  assert.match(typesSource, /plan\?: ProPlan \| null/);
});

test("in-app and public copy separate Local Pro from unavailable Pro Cloud", () => {
  for (const source of [activationSource, productSource, readmeSource, siteSource]) {
    assert.match(source, /Local Pro/);
    assert.match(source, /\$36\/year/);
    assert.match(source, /Pro Cloud/);
    assert.match(source, /\$20\/month/);
    assert.match(source, /includes\s+(?:everything\s+in\s+)?Local Pro/i);
    assert.match(source, /not (?:yet )?(?:available|open)/i);
  }

  for (const source of [readmeSource, siteSource]) {
    assert.doesNotMatch(source, /Pro Cloud is (?:live|available|active)/i);
    assert.match(source, /end-to-end encrypted sync/i);
    assert.match(source, /LAC cannot read the ciphertext/i);
    assert.match(source, /Hosted processing is (?:a )?separate/i);
    assert.match(source, /selected job inputs are decrypted/i);
  }
});

test("licensed Cloud status does not display the Local Pro price detail", () => {
  assert.match(productSource, /planPresentation\.kind === "cloud"/);
  assert.match(productSource, /Cloud account authority remains separate/);
  assert.match(productSource, /detail=\{planDetail\}/);
});

test("public delivery runbook records the tier and integrity contract without deploying it", () => {
  assert.match(deliverySource, /LOCAL_PRO_BENEFIT_ID/);
  assert.match(deliverySource, /PRO_CLOUD_BENEFIT_ID/);
  assert.match(deliverySource, /ARTIFACT_SHA256/);
  assert.match(deliverySource, /unrelated product/i);
  assert.match(deliverySource, /rate limiting binding|WAF rule/i);
  assert.match(deliverySource, /perform any account-backed action/i);
});
