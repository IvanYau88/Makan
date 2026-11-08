import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
const repo = "../../";
const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");
const canonical = (value) =>
  Array.isArray(value)
    ? `[${value.map(canonical).join(",")}]`
    : value && typeof value === "object"
      ? `{${Object.keys(value)
          .sort()
          .map((k) => `${JSON.stringify(k)}:${canonical(value[k])}`)
          .join(",")}}`
      : JSON.stringify(value);

// Each served state contract: its manifest, and the files the gateway imports from it.
const contracts = [
  {
    manifest: "src/makan/evals/sets/clef/manifest.json",
    files: [
      "src/makan/evals/sets/clef/contract.json",
      "src/makan/evals/sets/clef/state.schema.json",
    ],
  },
  {
    manifest: "src/makan/evals/sets/clef/v2/manifest.json",
    files: [
      "src/makan/evals/sets/clef/contract.json",
      "src/makan/evals/sets/clef/v2/state.schema.json",
      "src/makan/visits/history_policy.json",
    ],
  },
];
const config = readFileSync("src/config.ts", "utf8");
const hashes = [];
for (const { manifest: path, files } of contracts) {
  const manifest = JSON.parse(readFileSync(repo + path, "utf8"));
  for (const name of files) {
    if (sha256(readFileSync(repo + name)) !== manifest.files[name]) {
      throw new Error(
        "Frozen Clef contract changed; qualify and version before use",
      );
    }
  }
  const { content_sha256: pinned, ...rest } = manifest;
  if (sha256(canonical(rest)) !== pinned) {
    throw new Error("Clef manifest content hash does not match its content");
  }
  if (!config.includes(`"${pinned}"`)) {
    throw new Error("Gateway must pin the frozen manifest content hash");
  }
  hashes.push(pinned);
}
const v2 = JSON.parse(readFileSync(repo + contracts[1].manifest, "utf8"));
if (v2.extends !== hashes[0]) {
  throw new Error("State contract 2 must extend the frozen version 1 manifest");
}
