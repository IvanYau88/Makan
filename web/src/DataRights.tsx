import { useRef, useState } from "react";

interface Props {
  /** The text of the export file. Rejects with a message to show. */
  onExport: () => Promise<string>;
  /** Delete the person's data and account. Rejects with a message to show. */
  onDelete: () => Promise<void>;
}

const FILE_NAME = "makan-data.json";

/** Export and delete: the person's own data, in their hands. */
export function DataRights({ onExport, onDelete }: Props) {
  const [exporting, setExporting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [exported, setExported] = useState(false);
  const confirmHeading = useRef<HTMLHeadingElement>(null);
  const deleteButton = useRef<HTMLButtonElement>(null);

  const download = async () => {
    setFailure(null);
    setExported(false);
    setExporting(true);
    try {
      const text = await onExport();
      const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = FILE_NAME;
      link.click();
      URL.revokeObjectURL(url);
      setExported(true);
    } catch (e) {
      setFailure(e instanceof Error ? e.message : "Something went wrong. Try again.");
    } finally {
      setExporting(false);
    }
  };

  const remove = async () => {
    setFailure(null);
    setDeleting(true);
    try {
      await onDelete();
    } catch (e) {
      setFailure(e instanceof Error ? e.message : "Something went wrong. Try again.");
      setDeleting(false);
    }
  };

  return (
    <div className="account-form">
      <div className="data-row">
        <div>
          <h3 className="data-title">Export my data</h3>
          <p className="hint">
            Download everything Makan has stored for you as a file: your profile, your taste, and
            what it remembers, with when and how sure.
          </p>
        </div>
        <button
          type="button"
          className="button button-secondary"
          onClick={() => void download()}
          disabled={exporting}
        >
          {exporting ? "Preparing…" : "Download"}
        </button>
      </div>
      <p className="hint" role="status">
        {exported ? `Saved ${FILE_NAME}.` : ""}
      </p>

      <div className="data-row">
        <div>
          <h3 className="data-title">Delete my data and account</h3>
          <p className="hint">
            Removes your profile, your taste, and everything Makan remembers, and deletes your
            account. This cannot be undone.
          </p>
        </div>
        {!confirming && (
          <button
            ref={deleteButton}
            type="button"
            className="button button-danger"
            onClick={() => {
              setConfirming(true);
              setTimeout(() => confirmHeading.current?.focus(), 0);
            }}
          >
            Delete…
          </button>
        )}
      </div>

      {confirming && (
        <div className="notice notice-error confirm" role="group" aria-labelledby="confirm-title">
          <h3 id="confirm-title" className="data-title" tabIndex={-1} ref={confirmHeading}>
            Delete everything?
          </h3>
          <p>
            Your account and all of your data will be gone for good. You can export it first. Makan
            keeps working as a guest.
          </p>
          <div className="group-actions">
            <button
              type="button"
              className="button button-danger-solid"
              onClick={() => void remove()}
              disabled={deleting}
            >
              {deleting ? "Deleting…" : "Yes, delete everything"}
            </button>
            <button
              type="button"
              className="button"
              onClick={() => {
                setConfirming(false);
                setTimeout(() => deleteButton.current?.focus(), 0);
              }}
              disabled={deleting}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {failure && (
        <p className="notice notice-error" role="alert">
          {failure}
        </p>
      )}
    </div>
  );
}
