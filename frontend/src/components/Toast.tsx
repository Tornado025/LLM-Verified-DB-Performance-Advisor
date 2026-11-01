import {
  createContext,
  useCallback,
  useContext,
  useState,
  useEffect,
} from "react";
import type { ReactNode } from "react";
const ToastContext = createContext<(message: string) => void>(() => {});
export function ToastProvider({ children }: { children: ReactNode }) {
  const [messages, setMessages] = useState<{ id: number; text: string }[]>([]);
  useEffect(() => {
    const timer = setInterval(
      () => setMessages((old) => old.filter((m) => Date.now() - m.id < 5000)),
      500,
    );
    return () => clearInterval(timer);
  }, []);
  const notify = useCallback(
    (text: string) =>
      setMessages((old) => [...old, { id: Date.now(), text }].slice(-3)),
    [],
  );
  return (
    <ToastContext.Provider value={notify}>
      {children}
      <div className="toasts" aria-live="polite">
        {messages.map((message) => (
          <div className="toast" key={message.id}>
            {message.text}
            <button
              aria-label="Dismiss notification"
              onClick={() =>
                setMessages((old) => old.filter((m) => m.id !== message.id))
              }
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
export const useToast = () => useContext(ToastContext);
