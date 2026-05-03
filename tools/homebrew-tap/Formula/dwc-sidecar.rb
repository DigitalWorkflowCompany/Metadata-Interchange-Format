class DwcSidecar < Formula
  include Language::Python::Virtualenv

  desc "Per-clip film-industry metadata sidecar — CLI"
  homepage "https://ns.the-dwc.com/sidecar/"
  # url + sha256 are filled in on each release by .github/workflows/
  # homebrew-tap-bump.yml — see tools/homebrew-tap/README.md.
  url "https://github.com/DigitalWorkflowCompany/Metadata-Interchange-Format/releases/download/v0.3.0/dwc_sidecar-0.3.0.tar.gz"
  sha256 "REPLACE_WITH_SDIST_SHA256_FROM_RELEASE"
  license "MIT"

  depends_on "python@3.12"

  # Resource blocks for runtime deps. Regenerate with:
  #   brew update-python-resources Formula/dwc-sidecar.rb
  # which fetches current PyPI URLs + sha256s for every dependency in
  # pyproject.toml. The bump workflow runs this automatically on each
  # release; values below are placeholders for the first publish.

  resource "jsonschema" do
    url "https://files.pythonhosted.org/packages/source/j/jsonschema/jsonschema-4.23.0.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "rfc8785" do
    url "https://files.pythonhosted.org/packages/source/r/rfc8785/rfc8785-0.1.4.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "cryptography" do
    url "https://files.pythonhosted.org/packages/source/c/cryptography/cryptography-43.0.1.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "xxhash" do
    url "https://files.pythonhosted.org/packages/source/x/xxhash/xxhash-3.5.0.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "blake3" do
    url "https://files.pythonhosted.org/packages/source/b/blake3/blake3-0.4.1.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  resource "pyyaml" do
    url "https://files.pythonhosted.org/packages/source/p/pyyaml/PyYAML-6.0.2.tar.gz"
    sha256 "REPLACE_WITH_PYPI_SHA256"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    # Smoke test — formula is valid if the CLI can be imported and
    # exits cleanly on --help. We don't run `dwc validate` here because
    # it needs a working directory with example data.
    system bin/"dwc", "--help"
  end
end
