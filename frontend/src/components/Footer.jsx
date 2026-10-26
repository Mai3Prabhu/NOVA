const BUILD_YEAR = new Date().getFullYear();

export default function Footer() {
  return (
    <footer className="site-footer">
      <span>NOVA_001</span>
      <span>AUTONOMOUS DOCUMENT AGENT · BUILD {BUILD_YEAR}</span>
    </footer>
  );
}
