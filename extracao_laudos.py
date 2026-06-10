"""
extract_laudo.py
----------------
Extrai campos estruturados de um Certificado de Análise (PDF gerado pelo
Crystal Reports / SAP) e devolve um JSON para o n8n.

Uso via linha de comando (nó Execute Command do n8n):
    python3 extract_laudo.py /caminho/para/arquivo.pdf

NOTA TÉCNICA:
Nesses PDFs, o cabeçalho (PRODUTO/CODIGO/LOTE) é renderizado com DUAS
camadas de texto sobrepostas em tamanhos de fonte diferentes (ex: 7.35
para a descrição do produto e 7.80 para "PRODUTO:", código e "LOTE:").
A extração de texto linear (extract_text) embaralha essas camadas.
Por isso, este script lê os caracteres brutos (page.chars) e separa
as duas camadas pelo atributo 'size' antes de aplicar regex.
"""

import sys
import re
import json
import os
from datetime import datetime
from collections import Counter

try:
    import pdfplumber
except ImportError:
    print(json.dumps({"sucesso": False, "erro": "pdfplumber não instalado. Execute: pip install pdfplumber"}))
    sys.exit(1)


def normalizar_lote(valor: str) -> str:
    """Remove separadores de milhar do número de lote (ex: '122.588' -> '122588')."""
    return valor.replace(".", "").replace(",", "").strip()


def extrair_cabecalho(page) -> dict:
    """
    Extrai codigo, produto e lote a partir dos caracteres brutos da linha
    do cabeçalho, separando as duas camadas de texto sobrepostas por
    tamanho de fonte.
    """
    chars = page.chars

    # Linha do cabeçalho de produto/lote fica tipicamente entre top 138-145
    linha_chars = sorted(
        [c for c in chars if 138 < c['top'] < 145],
        key=lambda c: c['x0']
    )

    if not linha_chars:
        return {"codigo": None, "produto": None, "lote": None}

    tamanhos = Counter(round(c['size'], 2) for c in linha_chars)
    size_principal, _ = tamanhos.most_common(1)[0]

    # Camada A: descrição do produto (tamanho de fonte mais comum)
    texto_a = "".join(c['text'] for c in linha_chars if abs(c['size'] - size_principal) < 0.05)
    # Camada B: "PRODUTO:", código e "LOTE: NNN.NNN"
    texto_b = "".join(c['text'] for c in linha_chars if abs(c['size'] - size_principal) >= 0.05)

    produto = texto_a.strip() or None

    m_codigo = re.search(r'PRODUTO:(.+?)LOTE', texto_b)
    codigo = m_codigo.group(1).strip() if m_codigo else None

    m_lote = re.search(r'(\d{2,3}\.\d{3})', texto_b)
    lote = normalizar_lote(m_lote.group(1)) if m_lote else None

    return {"codigo": codigo, "produto": produto, "lote": lote}


def extrair_texto(page) -> str:
    """Extrai todo o texto da página (para datas, conclusão, etc)."""
    texto = page.extract_text()
    if not texto:
        raise ValueError("Nenhum texto extraído - o PDF pode ser baseado em imagem (scanned).")
    return texto


def extrair_campos(page) -> dict:
    campos = extrair_cabecalho(page)
    texto = extrair_texto(page)

    def buscar_data(padrao):
        m = re.search(padrao, texto)
        return m.group(1) if m else None

    campos["data_fabricacao"] = buscar_data(r'DATA DE FABRICAÇÃO:\s+(\d{2}/\d{2}/\d{4})')
    campos["data_validade"]   = buscar_data(r'DATA DE VALIDADE:\s+(\d{2}/\d{2}/\d{4})')
    campos["data_emissao"]    = buscar_data(r'Data de emissão do certificado de análise:\s+(\d{2}/\d{2}/\d{4})')

    m_conc = re.search(r'CONCLUSÃO\s*:\s*(.+)', texto)
    campos["conclusao"] = m_conc.group(1).strip() if m_conc else None
    campos["aprovado"]  = "APROVADO" in (campos["conclusao"] or "").upper()

    return campos


def main():
    caminho = None
    if len(sys.argv) > 1:
        caminho = sys.argv[1]
    elif os.environ.get("PDF_PATH"):
        caminho = os.environ["PDF_PATH"]

    if not caminho:
        print(json.dumps({
            "sucesso": False,
            "erro": "Nenhum arquivo informado. Passe o path como argumento: python3 extract_laudo.py <arquivo.pdf>"
        }))
        sys.exit(1)

    if not os.path.isfile(caminho):
        print(json.dumps({
            "sucesso": False,
            "erro": f"Arquivo não encontrado: {caminho}"
        }))
        sys.exit(1)

    try:
        with pdfplumber.open(caminho) as pdf:
            page = pdf.pages[0]
            campos = extrair_campos(page)

        payload = {
            "sucesso":          True,
            "arquivo":          os.path.basename(caminho),
            "caminho_completo": os.path.abspath(caminho),
            "extraido_em":      datetime.now().isoformat(timespec="seconds"),
            **campos,
        }

        obrigatorios = ["codigo", "lote", "produto"]
        ausentes = [c for c in obrigatorios if not payload.get(c)]
        if ausentes:
            payload["avisos"] = [f"Campo obrigatório não encontrado: {c}" for c in ausentes]

        print(json.dumps(payload, ensure_ascii=False, indent=2))

    except Exception as e:
        print(json.dumps({"sucesso": False, "erro": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()