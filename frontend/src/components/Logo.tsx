import logo from "../assets/logo.png";

export default function Logo({ className = "h-10" }: { className?: string }) {
  return (
    <img
      src={logo}
      alt="Mestre das Odds"
      className={`${className} w-auto rounded-xl object-contain`}
    />
  );
}
