# LotuWiki
 Link Wikibase items to Wikidata

Written by [User:DL2204](https://www.wikidata.org/wiki/Q57694630) with the help of [DeepSeek v3](https://www.deepseek.com/en/).

## Functions

This app gets list of items from any Wikibase, proposes matching Wikidata items, and writes manually selected matches to the Wikibase, or records the decision that the Wikibase item is not found on Wikidata.
* The app assumes that the Wikibase P1 property is of type externalID and is meant to express exact matches with Wikidata ([example](https://ehkultura.wikibase.cloud/wiki/Property:P1)).
* The decision of marking a Wikibase item to be missing on Wikidata is recorded writing the string "CREATE" to the P1 property. That can then be used for querying for those items that should be cloned on Wikidata (this app does not do that).
* Several tasks can be defined inside the app, each with specifications for
  * the Wikibase to query and to write the matches to
  * the SPARQL query to be used for retrieving items to match
  * the default language to perform label searches in Wikidata
  * the set of Wikidata properties to retrieve values and display them to the user in the aligment procedure
* See some [screenshots](screenshots)

## Installation

* Clone this repo
* Install required modules from [requirements.txt](requirements.txt)
* Fill in username(s) and password(s) in [config-private.py](config_private.py)
  * The wikibase user needs to be part of group "bots"

## Usage
* Run [app.py](app.py)
