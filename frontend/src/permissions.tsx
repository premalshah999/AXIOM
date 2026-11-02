import "./styles/onboarding.css";

// What "Send them for me" allows, in the words the person reads once, at the choice: onboarding's sending step and the Overview's
// Start dialog show these same words before the account and terms settings (apply_permissions.py) are turned on. The same words
// are in docs/FRIENDS.md: keep them together.
export const CONSENT_LEAD = "By choosing this you allow AXIOM to:";
export const CONSENT_ITEMS = [
  "create accounts at employers, in your name",
  "accept their terms and privacy notices in your name",
  "answer their questions from your profile",
  "use your own AI key and your own email to do it",
  "submit each application without asking you first",
];
export const CONSENT_CLOSE = "It is on you to check that this is OK with you, and with each employer's rules. You can pause or stop sending at any time.";

export function ConsentWords() {
  return (
    <div className="ax-ob-consent" role="note">
      <p>{CONSENT_LEAD}</p>
      <ul>
        {CONSENT_ITEMS.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <p>{CONSENT_CLOSE}</p>
    </div>
  );
}
