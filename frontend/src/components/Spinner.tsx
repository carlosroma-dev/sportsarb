export default function Spinner() {
  return (
    <div className="flex h-full w-full items-center justify-center py-20">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-line border-t-primary" />
    </div>
  );
}
