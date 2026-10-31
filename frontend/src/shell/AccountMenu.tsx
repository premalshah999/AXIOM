import { LogOut } from "lucide-react";
import { Menu } from "./Menu";

export type Who = { name?: string; email?: string };

/** The account: an initial in the top bar, opening a small menu with who is signed in and sign out. */
export function AccountMenu({ who, onSignOut }: { who: Who; onSignOut: () => void }) {
  const name = who.name || who.email || "Signed in";
  return (
    <Menu
      label={"Account: " + name}
      title={who.email || name}
      buttonClass="ax-avatar"
      button={<span aria-hidden="true">{name.slice(0, 1).toUpperCase()}</span>}
    >
      {(close) => (
        <>
          <p className="ax-menu-who">{name}</p>
          <button
            type="button"
            role="menuitem"
            className="ax-menu-item"
            onClick={() => {
              close();
              onSignOut();
            }}
          >
            <LogOut size={15} aria-hidden="true" />
            Sign out
          </button>
        </>
      )}
    </Menu>
  );
}
