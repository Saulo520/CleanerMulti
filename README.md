# Cleaner Multi-language

Script avançado para limpeza, organização e análise de projetos em múltiplas linguagens. Ele detecta automaticamente a linguagem de cada arquivo, identifica código morto, ajusta imports, gerencia snapshots e permite executar diversas ações de manutenção estrutural no projeto.

---

## ✨ Funcionalidades Principais

### 🔍 Detecção automática de linguagem

O script identifica a linguagem de cada arquivo através da extensão. Suporta:

* JavaScript / TypeScript
* Python
* Java
* C#
* C / C++
* Go
* PHP

### 📦 Sistema de Cache

A varredura salva informações para acelerar execuções posteriores.

### 🗂 Detecção de Dead Files

Aplica heurísticas por linguagem para detectar arquivos não referenciados.

### ❌ Imports Quebrados

Detecta imports que apontam para arquivos inexistentes.

### 🗃 Comentário ou Remoção de Imports

Você pode:

* Comentar imports que apontam para uma pasta específica.
* Remover completamente esses imports.

### 📁 Remover Pastas

Remove um diretório inteiro do projeto, com snapshot automático.

### 🔄 Mover/Renomear Arquivos

Move arquivos ajustando automaticamente imports relacionados.

### 🔙 Snapshots e Undo

Cria backups automáticos antes de operações destrutivas.
Permite desfazer até *12 operações anteriores*.

### 🧪 Dry-run

Visualize as alterações sem executá-las.

---

## 📁 Estrutura de Arquivos Monitora

O script considera por padrão:


src/


Você pode alterar isso via configuração.

---

## ⚙ Como Usar

### 📌 Ajuda Geral


python cleaner.py --help


### 🔍 Escanear projeto e mostrar dead files


python cleaner.py --scan


### 🗑 Remover imports que apontam para uma pasta


python cleaner.py --remove-imports caminho/da/pasta


### 💬 Comentar imports que apontam para uma pasta


python cleaner.py --comment-imports caminho/da/pasta


### 🧼 Limpeza completa


python cleaner.py --clean


Realiza: scan + dead files + imports quebrados.

### 🗃 Criar snapshot manualmente


python cleaner.py --snapshot


### ⏪ Desfazer última operação


python cleaner.py --undo


---

## 🛡 Safe Mode

Por padrão o script pede confirmação antes de operações destrutivas.
Você pode forçar sem perguntas usando:


--yes


---

## 📋 Configurações

Você pode editar as opções na variável DEFAULT_CONFIG:

* project_root
* excluded_dirs
* file_extensions_map
* safe_mode
* allow_undo_count

---

## 📚 Logs

Todos os logs são gravados automaticamente na pasta:


logs/


---

## 💡 Observações Importantes

* O script funciona em qualquer tamanho de projeto.
* Heurísticas de dead code não são perfeitas, mas muito úteis.
* Para linguagens compiladas (Java, C#, C++), a detecção de imports depende da estrutura de projeto.

---

## 📝 Licença

Uso livre. Ajuste conforme sua necessidade.
